from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


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


class BlendTicket(models.Model):
    """拼配下槽单单头：出库日、目标品种、出库千克、开单人、结案时刻。"""

    unloadDate = models.DateField("出库日")
    targetCultivar = models.CharField("目标品种名", max_length=80)
    unloadKg = models.DecimalField("出库千克", max_digits=10, decimal_places=2)
    createdBy = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="blend_tickets",
        verbose_name="开单人",
    )
    createdAt = models.DateTimeField("开单时刻", auto_now_add=True)
    closedAt = models.DateTimeField("结案时刻", null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "拼配下槽单"
        verbose_name_plural = "拼配下槽单"

    def __str__(self):
        return f"拼配下槽单#{self.pk}（{self.targetCultivar} {self.unloadKg}kg）"

    @property
    def is_closed(self):
        return self.closedAt is not None

    def lines_total(self):
        from django.db.models import Sum

        return self.lines.aggregate(total=Sum("countedKg"))["total"]


class BlendTicketLine(models.Model):
    """拼配下槽单明细行：所属下槽单、槽位、计入千克。"""

    ticket = models.ForeignKey(
        BlendTicket,
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
    countedKg = models.DecimalField("计入千克", max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]
        verbose_name = "拼配下槽明细"
        verbose_name_plural = "拼配下槽明细"
        constraints = [
            models.UniqueConstraint(
                fields=["ticket", "trough"],
                name="uniq_trough_per_blend_ticket",
            ),
        ]

    def __str__(self):
        return f"{self.ticket_id}:{self.trough} {self.countedKg}kg"
