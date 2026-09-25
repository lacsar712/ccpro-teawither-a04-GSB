from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import (
    BlendTicket,
    BlendTicketLine,
    Garden,
    Trough,
    WitherBatch,
)


class GardenForm(forms.ModelForm):
    class Meta:
        model = Garden
        fields = ["name", "altitudeBand", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "altitudeBand": forms.TextInput(attrs={"class": "input"}),
            "notes": forms.Textarea(attrs={"class": "input", "rows": 3}),
        }


class TroughForm(forms.ModelForm):
    class Meta:
        model = Trough
        fields = ["garden", "troughCode", "cultivar", "loadKg", "status"]
        widgets = {
            "garden": forms.Select(attrs={"class": "input"}),
            "troughCode": forms.TextInput(attrs={"class": "input"}),
            "cultivar": forms.TextInput(attrs={"class": "input"}),
            "loadKg": forms.NumberInput(attrs={"class": "input", "step": "0.01"}),
            "status": forms.Select(attrs={"class": "input"}),
        }


class WitherBatchForm(forms.ModelForm):
    class Meta:
        model = WitherBatch
        fields = [
            "trough",
            "startedAt",
            "targetMoisture",
            "actualMoisture",
            "rollGrade",
        ]
        widgets = {
            "trough": forms.Select(attrs={"class": "input"}),
            "startedAt": forms.DateTimeInput(
                attrs={"class": "input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "targetMoisture": forms.NumberInput(
                attrs={"class": "input", "step": "0.01"}
            ),
            "actualMoisture": forms.NumberInput(
                attrs={"class": "input", "step": "0.01"}
            ),
            "rollGrade": forms.TextInput(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["startedAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        if self.instance and self.instance.pk and self.instance.startedAt:
            from django.utils import timezone

            local = timezone.localtime(self.instance.startedAt)
            self.initial["startedAt"] = local.strftime("%Y-%m-%dT%H:%M")

    def clean_trough(self):
        trough = self.cleaned_data["trough"]
        closed_qs = BlendTicketLine.objects.filter(
            trough=trough,
            ticket__closedAt__isnull=False,
        )
        if closed_qs.exists():
            # 已结案拼配单涉及的槽位禁止再开新批次；
            # 但允许继续编辑该槽上既有的批次（未换槽）。
            staying = (
                self.instance
                and self.instance.pk
                and self.instance.trough_id == trough.pk
            )
            if not staying:
                raise forms.ValidationError(
                    "该槽位已随拼配下槽单结案，不得再新建萎凋批次。"
                )
        return trough


class BlendTicketForm(forms.ModelForm):
    class Meta:
        model = BlendTicket
        fields = ["unloadDate", "targetCultivar", "unloadKg"]
        widgets = {
            "unloadDate": forms.DateInput(
                attrs={"class": "input", "type": "date"}, format="%Y-%m-%d"
            ),
            "targetCultivar": forms.TextInput(attrs={"class": "input"}),
            "unloadKg": forms.NumberInput(attrs={"class": "input", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unloadDate"].input_formats = ["%Y-%m-%d"]

    def clean_unloadKg(self):
        kg = self.cleaned_data["unloadKg"]
        if kg is not None and kg <= 0:
            raise forms.ValidationError("出库千克必须大于 0。")
        return kg


class BlendLineForm(forms.ModelForm):
    class Meta:
        model = BlendTicketLine
        fields = ["trough", "countedKg"]
        widgets = {
            "trough": forms.Select(attrs={"class": "input"}),
            "countedKg": forms.NumberInput(
                attrs={"class": "input", "step": "0.01"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trough"].queryset = Trough.objects.select_related("garden")

    def clean_countedKg(self):
        kg = self.cleaned_data["countedKg"]
        if kg is not None and kg <= 0:
            raise forms.ValidationError("计入千克必须大于 0。")
        return kg


class BaseBlendLineFormSet(BaseInlineFormSet):
    """明细行整体校验：可下槽、未挂其他未结案单、合计=出库千克（误差 0）。"""

    def clean(self):
        # 先跑自定义校验（中文报错更清晰），最后再用父类的
        # validate_unique 兜底（同单槽位唯一约束）。
        if any(self.errors):
            return

        ticket = self.instance
        total = Decimal("0")
        seen = set()
        rows = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            trough = form.cleaned_data.get("trough")
            kg = form.cleaned_data.get("countedKg")
            if trough is None and kg is None:
                continue
            if trough is None or kg is None:
                return  # 单字段错误已由行内表单报出
            rows += 1
            if trough.pk in seen:
                raise ValidationError(
                    f"槽位 {trough} 在同一单中重复出现，请合并计入千克。"
                )
            seen.add(trough.pk)
            if trough.status != Trough.STATUS_READY:
                raise ValidationError(
                    f"槽位 {trough} 当前状态为「{trough.get_status_display()}」，"
                    "不是可下槽，拒绝开单。"
                )
            conflict = (
                BlendTicketLine.objects.filter(
                    trough=trough,
                    ticket__closedAt__isnull=True,
                )
                .exclude(ticket_id=ticket.pk)
                .select_related("ticket")
                .first()
            )
            if conflict is not None:
                raise ValidationError(
                    f"槽位 {trough} 已挂在未结案的拼配下槽单"
                    f"#{conflict.ticket_id} 中，不得重复开单。"
                )
            total += kg

        if rows == 0:
            raise ValidationError("至少需要一条明细行。")
        if ticket.unloadKg is not None and total != ticket.unloadKg:
            raise ValidationError(
                f"计入千克合计 {total} 与出库千克 {ticket.unloadKg} 不一致，"
                "误差须为 0，拒绝开单。"
            )
        super().clean()


BlendLineFormSet = inlineformset_factory(
    BlendTicket,
    BlendTicketLine,
    form=BlendLineForm,
    formset=BaseBlendLineFormSet,
    extra=4,
    can_delete=False,
)
