from django.shortcuts import render
from django.conf import settings

from content.models import Article, Recording, Project, CuratedLink


TESTIMONIALS = [
    {
        'quote': 'This course helped me understand how to implement a RAG system in Python. From basic system-design of a RAG, to evaluating responses and implementing guardrails, the course gave me a great overview of the necessary skills for implementing and managing my own agent.',
        'name': 'Rolando',
        'role': 'AI Data Scientist',
        'company': 'AeroMexico',
    },
    {
        'quote': 'I highly recommend the AI Engineering Buildcamp. I learned a tremendous amount. The material is abundant, very well organized, and progresses in a logical and progressive manner. This made complex topics much easier to follow and digest. The instructor Alexey Grigorev is clearly very knowledgeable in the field, and also super helpful and responsive to questions.',
        'name': 'John',
        'role': 'AI Tutor',
        'company': 'Meta',
    },
    {
        'quote': 'Excellent, comprehensive, and modern course that elevated my knowledge of generative AI from RAG applications to well-evaluated, fully functioning agentic systems. Alexey Grigorev incorporated essential software engineering practices, especially unit testing and evaluation, teaching us how to systematically improve our agents.',
        'name': 'Yan',
        'role': 'Senior Data Scientist',
        'company': 'Virtualitics',
    },
    {
        'quote': "I really enjoyed this course! It made the process of building AI agents both accessible and exciting. The progression from RAG to agents, multi-agent systems, monitoring, and guardrails was clear and practical. I'm walking away inspired and full of new ideas to build on.",
        'name': 'Scott',
        'role': 'Principal Data Scientist, Applied AI',
        'company': 'interos.ai',
    },
    {
        'quote': 'The course provides an excellent introduction to the core tooling needed to develop an agentic tool. Worth the effort especially given the comprehensiveness of the options and solutions available in the course.',
        'name': 'Naveen',
        'role': 'Software Engineer',
        'company': '',
    },
    {
        'quote': 'Excellent course, it gets you practicing the concepts you need to know to work on agentic AI. The instructor is accessible, clear, and flexible.',
        'name': 'Nelson',
        'role': 'Practitioner',
        'company': '',
    },
]

FEATURES = [
    {
        'icon': 'hammer',
        'title': 'Learning by doing',
        'description': 'No passive consumption. Every activity is designed around building, shipping, and getting feedback on real work.',
    },
    {
        'icon': 'rocket',
        'title': 'Production-ready',
        'description': 'Focus on what actually works in production. Move from prototypes to reliable systems with battle-tested patterns.',
    },
    {
        'icon': 'users',
        'title': 'Build together',
        'description': 'Work alongside other practitioners. Hackathons, projects, and group problem-solving instead of isolated learning.',
    },
    {
        'icon': 'brain',
        'title': 'Calibrate your judgment',
        'description': 'Develop better instincts through peer feedback, expert guidance, and exposure to real-world decision-making patterns.',
    },
]

TIERS = [
    {
        'name': 'Basic',
        'stripe_key': 'basic',
        'tagline': 'Content only',
        'description': 'Access curated educational content, tutorials, and research. Perfect for self-directed builders who learn at their own pace.',
        'price_monthly': 20,
        'price_annual': 200,
        'hook': 'Educational content without community access.',
        'features': [
            {'text': 'Full access to exclusive Substack content', 'included': True},
            {'text': 'Hands-on tutorials with code examples you can implement', 'included': True},
            {'text': 'Curated breakdowns of new AI tools and workflows', 'included': True},
            {'text': 'Behind-the-scenes access to ongoing research and experiments', 'included': True},
            {'text': 'Curated collection of valuable social posts you might have missed', 'included': True},
        ],
        'positioning': 'Best for independent builders who prefer self-paced learning. Upgrade to Main for structure, accountability, and community support.',
        'highlighted': False,
    },
    {
        'name': 'Main',
        'stripe_key': 'main',
        'tagline': 'Live learning + community',
        'description': 'Everything in Basic, plus the structure, accountability, and peer support to ship your AI projects consistently.',
        'price_monthly': 50,
        'price_annual': 500,
        'hook': 'Build with the community and get the accountability and direction you need to make progress.',
        'features': [
            {'text': 'Everything in Basic', 'included': True},
            {'text': 'Closed community access to connect and interact with practitioners', 'included': True},
            {'text': 'Collaborative problem-solving and mentorship for implementation challenges', 'included': True},
            {'text': 'Interactive group coding sessions led by a host', 'included': True},
            {'text': 'Guided project-based learning with curated resources', 'included': True},
            {'text': 'Community hackathons', 'included': True},
            {'text': 'Career advancement discussions and feedback', 'included': True},
            {'text': 'Personal brand development guidance and content', 'included': True},
            {'text': 'Developer productivity tips and workflows', 'included': True},
            {'text': 'Propose and vote on future topics', 'included': True},
        ],
        'positioning': 'Best for builders who need structure and accountability to turn project ideas into reality alongside motivated peers.',
        'highlighted': True,
    },
    {
        'name': 'Premium',
        'stripe_key': 'premium',
        'tagline': 'Courses + personalized feedback',
        'description': 'Everything in Main, plus structured learning paths through mini-courses and personalized career guidance to accelerate your growth.',
        'price_monthly': 100,
        'price_annual': 1000,
        'hook': 'Accelerate your growth with structured courses and personalized feedback.',
        'features': [
            {'text': 'Everything in Main', 'included': True},
            {'text': 'Access to all mini-courses on specialized topics', 'included': True},
            {'text': 'Collection regularly updated with new courses', 'included': True},
            {'text': 'Upcoming: Python for Data and AI Engineering', 'included': True},
            {'text': 'Propose and vote on mini-course topics', 'included': True},
            {'text': 'Resume, LinkedIn, and GitHub teardowns', 'included': True},
        ],
        'positioning': 'Best for builders seeking structured learning paths to complement hands-on projects, plus personalized career guidance.',
        'highlighted': False,
    },
]

FAQ_ITEMS = [
    {
        'question': 'Who is this community for?',
        'answer': "Action-oriented builders interested in AI engineering and AI tools who want to turn ideas into real projects. Whether you're learning Python or working as an ML engineer, if you have project ideas but need structure, focus, and accountability, this community is for you. We attract motivated learners who prefer learning by doing and builders who contribute back to the ecosystem.",
    },
    {
        'question': 'What makes this different from other tech communities?',
        'answer': 'We focus on helping you ship practical AI products, not just consume content. You get clear frameworks, direction, and gentle external pressure to make consistent progress on your projects. The community concentrates highly engaged builders in a focused environment centered on productivity, structured execution, and hands-on project work.',
    },
    {
        'question': 'I have a main job. Can I still participate?',
        'answer': 'Yes. The community is designed to help you make consistent progress on side projects even with limited time. You get the structure and accountability to stay focused and ship incrementally through projects, hackathons, and collaborative activities.',
    },
    {
        'question': 'What if I just want the content without community?',
        'answer': 'The Basic tier is designed exactly for this. You get access to exclusive content, tutorials, research, and curated materials without any expectation of community participation. Perfect for self-directed builders who learn at their own pace.',
    },
    {
        'question': "What's included in the Main tier?",
        'answer': 'Main tier gives you the structure, accountability, and peer support to ship your AI projects consistently. Includes everything in Basic, plus closed community access, collaborative problem-solving, interactive group coding sessions, guided projects, hackathons, career discussions, and the ability to propose and vote on topics.',
    },
    {
        'question': "What's included in the Premium tier?",
        'answer': 'Premium tier accelerates your growth with structured learning paths through mini-courses and personalized career guidance. Includes everything in Main, plus access to all mini-courses on specialized topics, the ability to vote on course topics, and professional profile teardowns (resume, LinkedIn, GitHub).',
    },
    {
        'question': 'How do I get started?',
        'answer': "Pick the tier that fits your needs, click the button to check out securely via Stripe, and you'll receive access details by email within 24 hours. You can start with any tier and upgrade or downgrade at any time.",
    },
    {
        'question': 'How does billing work?',
        'answer': "All payments are processed securely through Stripe. You can choose monthly or annual billing (annual saves ~17%). Stripe handles tax calculation automatically based on your location. You'll receive invoices and receipts by email after each payment.",
    },
    {
        'question': 'Can I cancel or change my subscription?',
        'answer': "Yes, you're in full control. You can cancel, upgrade, downgrade, or update your payment method at any time through the Stripe Customer Portal. If you cancel, you'll retain access until the end of your current billing period.",
    },
]

SECTION_NAV = [
    {'id': 'about', 'label': 'Philosophy'},
    {'id': 'tiers', 'label': 'Membership'},
    {'id': 'testimonials', 'label': 'Testimonials'},
    {'id': 'resources', 'label': 'Workshops'},
    {'id': 'blog', 'label': 'Blog'},
    {'id': 'projects', 'label': 'Projects'},
    {'id': 'collection', 'label': 'Curated Links'},
    {'id': 'newsletter', 'label': 'Newsletter'},
    {'id': 'faq', 'label': 'FAQ'},
]


def home(request):
    """Homepage view."""
    articles = Article.objects.filter(published=True)[:3]
    recordings = Recording.objects.filter(published=True)[:3]
    projects = Project.objects.filter(published=True)[:3]
    curated_links = CuratedLink.objects.filter(published=True)[:6]

    # Add payment links to tiers
    stripe_links = settings.STRIPE_PAYMENT_LINKS
    tiers_with_links = []
    for tier in TIERS:
        tier_copy = dict(tier)
        key = tier['stripe_key']
        tier_copy['payment_link_monthly'] = stripe_links.get(key, {}).get('monthly', '#')
        tier_copy['payment_link_annual'] = stripe_links.get(key, {}).get('annual', '#')
        tiers_with_links.append(tier_copy)

    context = {
        'articles': articles,
        'recordings': recordings,
        'projects': projects,
        'curated_links': curated_links,
        'testimonials': TESTIMONIALS,
        'features': FEATURES,
        'tiers': tiers_with_links,
        'faq_items': FAQ_ITEMS,
        'section_nav': SECTION_NAV,
    }
    return render(request, 'home.html', context)
