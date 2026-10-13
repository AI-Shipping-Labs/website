from django.urls import path

from booking.views.public import (
    booking_cancel,
    booking_confirmed,
    booking_ics,
    booking_index,
    booking_reschedule,
    host_booking,
    host_confirm,
    host_slots,
)

urlpatterns = [
    path("book", booking_index, name="booking_index"),
    path("book/confirmed/<str:token>", booking_confirmed, name="booking_confirmed"),
    path("book/reschedule/<str:token>", booking_reschedule, name="booking_reschedule"),
    path("book/cancel/<str:token>", booking_cancel, name="booking_cancel"),
    path("book/ics/<str:token>", booking_ics, name="booking_ics"),
    path("book/<slug:slug>", host_booking, name="booking_host"),
    path("book/<slug:slug>/slots", host_slots, name="booking_slots"),
    path("book/<slug:slug>/confirm", host_confirm, name="booking_confirm"),
]
