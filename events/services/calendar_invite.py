"""Calendar invite (.ics) generation for events."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from icalendar import Calendar, vCalAddress, vText
from icalendar import Event as ICalEvent


def generate_ics(event, method='REQUEST'):
    """Generate a .ics calendar file for an event.

    Args:
        event: Event model instance.
        method: iCalendar method ('REQUEST' for new/update, 'CANCEL' for cancellation).

    Returns:
        bytes: The .ics file content.
    """
    cal = Calendar()
    cal.add('prodid', '-//AI Shipping Labs//Events//EN')
    cal.add('version', '2.0')
    cal.add('method', method)

    vevent = ICalEvent()
    vevent.add('summary', event.title)
    vevent.add('dtstart', event.start_datetime)

    # Use end_datetime if set, otherwise default to start + 1 hour
    end_dt = event.end_datetime or (event.start_datetime + timedelta(hours=1))
    vevent.add('dtend', end_dt)

    vevent.add('dtstamp', timezone.now())
    vevent.add('sequence', event.ics_sequence)

    # Stable UID per event
    vevent.add('uid', f'event-{event.slug}@aishippinglabs.com')

    # Description (plain text)
    if event.description:
        vevent.add('description', event.description)

    # Join URL
    site_url = getattr(settings, 'SITE_URL', 'https://aishippinglabs.com')
    join_url = f'{site_url}/events/{event.slug}/join'
    vevent.add('url', join_url)
    vevent.add('location', vText(join_url))

    # Organizer
    from_email = getattr(settings, 'SES_FROM_EMAIL', 'community@aishippinglabs.com')
    organizer = vCalAddress(f'mailto:{from_email}')
    organizer.params['cn'] = vText('AI Shipping Labs')
    vevent.add('organizer', organizer)

    cal.add_component(vevent)

    return cal.to_ical()
