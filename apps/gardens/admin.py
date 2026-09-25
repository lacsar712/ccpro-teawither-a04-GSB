from django.contrib import admin

from .models import BlendTicket, BlendTicketLine, Garden, Trough, WitherBatch


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


class BlendTicketLineInline(admin.TabularInline):
    model = BlendTicketLine
    extra = 0


@admin.register(BlendTicket)
class BlendTicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "unloadDate",
        "targetCultivar",
        "unloadKg",
        "createdBy",
        "closedAt",
    )
    list_filter = ("closedAt",)
    inlines = [BlendTicketLineInline]


@admin.register(BlendTicketLine)
class BlendTicketLineAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "trough", "countedKg")
