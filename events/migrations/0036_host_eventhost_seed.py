# Generated for issue #994 on 2026-06-16

import django.db.models.deletion
from django.db import migrations, models

from content.utils.markdown import render_markdown

ALEXEY_BIO = """Software engineer and machine learning practitioner with 15+ years of experience building production ML systems. I focus on practical, production-grade ML and AI systems, from early prototypes to reliable systems in production.

I'm the founder of DataTalks.Club, a free community that connects tens of thousands of practitioners worldwide, and the creator of the Zoomcamp series, free, code-first programs that have reached 100,000+ learners globally.

At AI Shipping Labs, I'm building the kind of environment that would have accelerated my own career growth. After years of teaching at scale, I wanted something more focused: a space for action-oriented builders who want to turn AI ideas into real projects. The community gives members the structure, accountability, and peer support to ship practical AI products consistently, even alongside their main jobs."""

VALERIIA_BIO = """Content strategist and technical writer specializing in AI/ML education. I focus on making complex technical concepts accessible and helping builders learn through clear, practical content.

At AI Shipping Labs, I work alongside Alexey to shape the community's content strategy and member experience. I ensure that motivated learners have the resources, frameworks, and clear direction they need to make consistent progress on their AI projects. My goal is to help builders bridge the gap from ideas to shipped products by providing structure and removing friction from the learning-by-doing process."""

SEED_HOSTS = [
    {
        "slug": "alexey-grigorev",
        "name": "Alexey Grigorev",
        "bio": ALEXEY_BIO,
        "email": "alexey@aishippinglabs.com",
        "photo_url": "",
        "is_active": True,
    },
    {
        "slug": "valeriia-kuka",
        "name": "Valeriia Kuka",
        "bio": VALERIIA_BIO,
        "email": "valeriia@aishippinglabs.com",
        "photo_url": "",
        "is_active": True,
    },
]


def _host_defaults(data):
    return {
        **data,
        "bio_html": (
            render_markdown(data["bio"], include_external_links=False)
            if data["bio"]
            else ""
        ),
    }


def seed_hosts(apps, schema_editor):
    Host = apps.get_model("events", "Host")

    for data in SEED_HOSTS:
        Host.objects.update_or_create(
            slug=data["slug"],
            defaults=_host_defaults(data),
        )


def unseed_hosts(apps, schema_editor):
    Host = apps.get_model("events", "Host")
    EventHost = apps.get_model("events", "EventHost")
    slugs = [host["slug"] for host in SEED_HOSTS]
    host_ids = list(Host.objects.filter(slug__in=slugs).values_list("id", flat=True))
    EventHost.objects.filter(host_id__in=host_ids).delete()
    Host.objects.filter(id__in=host_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0035_remove_event_max_participants"),
    ]

    operations = [
        migrations.CreateModel(
            name="Host",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=200, unique=True)),
                ("bio", models.TextField(blank=True, default="", help_text="Markdown bio rendered to HTML on save.")),
                ("bio_html", models.TextField(blank=True, default="", editable=False, help_text="Auto-rendered HTML from bio markdown.")),
                ("photo_url", models.URLField(blank=True, default="", help_text="Photo URL. Falls back to a static asset for seeded hosts.", max_length=500)),
                ("email", models.EmailField(blank=True, default="", help_text="Display/contact email only; not used for calendar invites.", max_length=254)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="EventHost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField(default=0, help_text="Display order; 0 is the primary host.")),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="event_host_links", to="events.event")),
                ("host", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="event_host_links", to="events.host")),
            ],
            options={
                "ordering": ["position"],
                "unique_together": {("event", "host")},
            },
        ),
        migrations.AddField(
            model_name="event",
            name="hosts",
            field=models.ManyToManyField(blank=True, help_text="Hosts for this event. Order is controlled via the EventHost.position field.", related_name="events", through="events.EventHost", to="events.host"),
        ),
        migrations.RunPython(
            seed_hosts,
            unseed_hosts,
        ),
    ]
