from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    BlendUnloadLine,
    BlendUnloadTicket,
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
        else:
            # 新建批次：已被结案拼配单占用的槽不得再开批次，直接不给选。
            blocked = Trough.objects.filter(
                blend_lines__ticket__closedAt__isnull=False
            ).values_list("pk", flat=True)
            self.fields["trough"].queryset = Trough.objects.exclude(
                pk__in=list(blocked)
            )


# ---- 拼配下槽单 ----


class BlendTicketForm(forms.ModelForm):
    class Meta:
        model = BlendUnloadTicket
        fields = ["unloadDate", "targetCultivar", "outKg"]
        widgets = {
            "unloadDate": forms.DateInput(
                attrs={"class": "input", "type": "date"}
            ),
            "targetCultivar": forms.TextInput(attrs={"class": "input"}),
            "outKg": forms.NumberInput(
                attrs={"class": "input", "step": "0.01", "min": "0"}
            ),
        }


class BlendLineForm(forms.ModelForm):
    class Meta:
        model = BlendUnloadLine
        fields = ["trough", "countKg"]
        widgets = {
            "trough": forms.Select(attrs={"class": "input"}),
            "countKg": forms.NumberInput(
                attrs={"class": "input", "step": "0.01", "min": "0"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 开单时每个明细槽必须已是可下槽。
        self.fields["trough"].queryset = Trough.objects.filter(
            status=Trough.STATUS_READY
        ).select_related("garden")


BlendLineFormSet = forms.inlineformset_factory(
    BlendUnloadTicket,
    BlendUnloadLine,
    form=BlendLineForm,
    fields=["trough", "countKg"],
    extra=2,
    can_delete=False,
    validate_min=True,
    min_num=1,
)


def validate_and_save_blend(ticket_form, line_formset, user):
    """校验单头 + 明细的全部业务规则并原子保存，返回已建单据。

    规则：
    1. 至少一条明细；
    2. 每个明细槽开单时必须是「可下槽」；
    3. 同一槽未结案时不可再入别的未结案单（单内也不得重复挂同一槽）；
    4. 明细计入千克之和必须与出库千克完全相等，误差为 0。
    """
    if not ticket_form.is_valid() or not line_formset.is_valid():
        return None

    out_kg = ticket_form.cleaned_data["outKg"]
    lines = [
        f.cleaned_data
        for f in line_formset
        if f.cleaned_data and not f.cleaned_data.get("DELETE")
    ]
    if not lines:
        line_formset.non_form_errors().append(
            ValidationError("至少填写一条下槽明细。")
        )
        return None

    troughs = [line["trough"] for line in lines]

    seen = set()
    for trough in troughs:
        if trough.pk in seen:
            line_formset.non_form_errors().append(
                ValidationError(f"槽位「{trough}」在同一单内重复出现。")
            )
            return None
        seen.add(trough.pk)

    # 重新落库核对状态，避免表单选项过期。
    ready_ids = set(
        Trough.objects.filter(
            pk__in=[t.pk for t in troughs], status=Trough.STATUS_READY
        ).values_list("pk", flat=True)
    )
    for trough in troughs:
        if trough.pk not in ready_ids:
            line_formset.non_form_errors().append(
                ValidationError(f"槽位「{trough}」当前不是「可下槽」状态，拒绝开单。")
            )
            return None

    busy = (
        Trough.objects.filter(pk__in=ready_ids)
        .filter(
            blend_lines__isnull=False,
            blend_lines__ticket__closedAt__isnull=True,
        )
        .distinct()
    )
    busy_names = [str(t) for t in busy]
    if busy_names:
        line_formset.non_form_errors().append(
            ValidationError(
                "以下槽位已挂在其他未结案拼配单上：" + "、".join(busy_names)
            )
        )
        return None

    total = sum((line["countKg"] for line in lines), Decimal("0"))
    if total != out_kg:
        ticket_form.add_error(
            "outKg",
            f"明细计入千克合计 {total}kg 与出库千克 {out_kg}kg 不一致，误差须为 0，拒绝开单。",
        )
        return None

    with transaction.atomic():
        ticket = ticket_form.save(commit=False)
        ticket.openedBy = user
        ticket.save()
        line_formset.instance = ticket
        saved_lines = line_formset.save(commit=False)
        for line in saved_lines:
            line.ticket = ticket
            line.save()
        return ticket
