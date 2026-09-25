from django.contrib import admin

from .models import (
    BlendUnloadLine,
    BlendUnloadTicket,
    Garden,
    Trough,
    WitherBatch,
)


@admin.register(Garden)
class GardenAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "altitudeBand")
    search_fields = ("name", "altitudeBand")


@admin.register(Trough)
class TroughAdmin(admin.ModelAdmin):
    list_display = ("id", "garden", "troughCode", "cultivar", "loadKg", "status")
    list_filter = ("status", "garden")
    search_fields = ("troughCode", "cultivar")


@admin.register(WitherBatch)
class WitherBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "trough",
        "startedAt",
        "targetMoisture",
        "actualMoisture",
        "rollGrade",
    )
    list_filter = ("rollGrade",)


class BlendUnloadLineInline(admin.TabularInline):
    model = BlendUnloadLine
    extra = 1


@admin.register(BlendUnloadTicket)
class BlendUnloadTicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "unloadDate",
        "targetCultivar",
        "outKg",
        "openedBy",
        "openedAt",
        "closedAt",
    )
    list_filter = ("closedAt",)
    search_fields = ("targetCultivar",)
    inlines = [BlendUnloadLineInline]
