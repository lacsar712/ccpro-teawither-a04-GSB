from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Garden(models.Model):
    name = models.CharField("茶园名称", max_length=120)
    altitudeBand = models.CharField("海拔带", max_length=60)
    notes = models.TextField("备注", blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = "茶园"
        verbose_name_plural = "茶园"

    def __str__(self):
        return self.name


class Trough(models.Model):
    STATUS_LOADING = "loading"
    STATUS_WITHERING = "withering"
    STATUS_READY = "ready"
    STATUS_CHOICES = [
        (STATUS_LOADING, "装叶中"),
        (STATUS_WITHERING, "萎凋中"),
        (STATUS_READY, "可下槽"),
    ]

    garden = models.ForeignKey(
        Garden,
        on_delete=models.CASCADE,
        related_name="troughs",
        verbose_name="茶园",
    )
    troughCode = models.CharField("槽位编号", max_length=40)
    cultivar = models.CharField("茶树品种", max_length=80)
    loadKg = models.DecimalField("装叶量(kg)", max_digits=10, decimal_places=2)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_LOADING,
    )

    class Meta:
        ordering = ["garden__name", "troughCode"]
        verbose_name = "萎凋槽"
        verbose_name_plural = "萎凋槽"
        constraints = [
            models.UniqueConstraint(
                fields=["garden", "troughCode"],
                name="uniq_trough_code_per_garden",
            ),
        ]

    def __str__(self):
        return f"{self.garden.name}-{self.troughCode}"

    def latest_batch(self):
        return self.batches.order_by("-startedAt", "-id").first()

    def clean(self):
        super().clean()
        if self.status != self.STATUS_READY:
            return
        latest = None
        if self.pk:
            latest = (
                WitherBatch.objects.filter(trough_id=self.pk)
                .order_by("-startedAt", "-id")
                .first()
            )
        if latest is None or latest.actualMoisture is None or latest.actualMoisture > 40:
            raise ValidationError(
                {
                    "status": "无法设为可下槽：最新萎凋批次的实测含水率为空或高于 40%。"
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def open_blend_ticket(self):
        """该槽当前挂入的未结案拼配下槽单（无则 None）。"""
        line = (
            self.blend_lines.filter(ticket__closedAt__isnull=True)
            .select_related("ticket")
            .first()
        )
        return line.ticket if line else None

    def has_closed_blend(self):
        """该槽是否已被某张已结案拼配下槽单占用（结案后禁开批次）。"""
        return self.blend_lines.filter(ticket__closedAt__isnull=False).exists()


class WitherBatch(models.Model):
    trough = models.ForeignKey(
        Trough,
        on_delete=models.CASCADE,
        related_name="batches",
        verbose_name="萎凋槽",
    )
    startedAt = models.DateTimeField("开始时间")
    targetMoisture = models.DecimalField(
        "目标含水率(%)", max_digits=5, decimal_places=2
    )
    actualMoisture = models.DecimalField(
        "实测含水率(%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rollGrade = models.CharField("揉捻等级", max_length=40)

    class Meta:
        ordering = ["-startedAt", "-id"]
        verbose_name = "萎凋批次"
        verbose_name_plural = "萎凋批次"

    def __str__(self):
        return f"{self.trough} @ {self.startedAt:%Y-%m-%d %H:%M}"

    def clean(self):
        super().clean()
        # 仅拦截“新建”：结案后相关槽不得再开萎凋批次；既有批次仍可按原规则修改。
        if not self.pk and self.trough_id and self.trough.has_closed_blend():
            raise ValidationError(
                {
                    "trough": "该槽位的拼配下槽单已结案，结案后不得再新建萎凋批次。"
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class BlendUnloadTicket(models.Model):
    """拼配下槽单：一次出库可挂多条可下槽槽位。"""

    unloadDate = models.DateField("出库日")
    targetCultivar = models.CharField("目标品种名", max_length=80)
    outKg = models.DecimalField("出库千克", max_digits=10, decimal_places=2)
    openedBy = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="blend_tickets",
        verbose_name="开单人",
    )
    openedAt = models.DateTimeField("开单时刻", auto_now_add=True)
    closedAt = models.DateTimeField("结案时刻", null=True, blank=True)

    class Meta:
        ordering = ["-unloadDate", "-id"]
        verbose_name = "拼配下槽单"
        verbose_name_plural = "拼配下槽单"

    def __str__(self):
        return f"{self.unloadDate:%Y-%m-%d} {self.targetCultivar} {self.outKg}kg"

    @property
    def is_closed(self):
        return self.closedAt is not None

    def line_kg_sum(self):
        total = self.lines.aggregate(total=Sum("countKg"))["total"]
        return total if total is not None else Decimal("0")


class BlendUnloadLine(models.Model):
    """下槽单明细：某张单挂入的槽位及计入千克。"""

    ticket = models.ForeignKey(
        BlendUnloadTicket,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="所属下槽单",
    )
    trough = models.ForeignKey(
        Trough,
        on_delete=models.PROTECT,
        related_name="blend_lines",
        verbose_name="槽位",
    )
    countKg = models.DecimalField("计入千克", max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]
        verbose_name = "拼配下槽明细"
        verbose_name_plural = "拼配下槽明细"

    def __str__(self):
        return f"{self.trough} → {self.countKg}kg"

    def clean(self):
        super().clean()
        if self.countKg is not None and self.countKg <= 0:
            raise ValidationError({"countKg": "计入千克必须大于 0。"})
        if self.trough_id and self.trough.status != Trough.STATUS_READY:
            raise ValidationError({"trough": "槽位不是「可下槽」状态，不能开单。"})
        if self.trough_id:
            qs = BlendUnloadLine.objects.filter(
                trough_id=self.trough_id,
                ticket__closedAt__isnull=True,
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if self.ticket_id:
                qs = qs.exclude(ticket_id=self.ticket_id)
            occupied = qs.select_related("ticket").first()
            if occupied is not None:
                raise ValidationError(
                    {
                        "trough": (
                            f"槽位已挂在未结案拼配单「{occupied.ticket}」上，"
                            "结案前不能再入别的未结案单。"
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
