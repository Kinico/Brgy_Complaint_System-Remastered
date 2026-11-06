"""Turns complaint counts into plain recommendations for barangay officials.

The dashboard ranks the categories with the most complaints in the last 30 days,
compares them with the 30 days before, names the most reported location, and
attaches the action the barangay set for that category. Officials can use this
as a basis for ordinances, patrol schedules, or clean-up drives.
"""
from collections import Counter
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from .models import Category, Complaint

DEFAULT_ACTIONS = {
    'noise': 'Schedule tanod patrols during the reported hours and remind establishments of the barangay quiet hours ordinance.',
    'garbage': 'Coordinate with the city garbage collection schedule and hold a clean-up drive in the most reported areas.',
    'waste': 'Coordinate with the city garbage collection schedule and hold a clean-up drive in the most reported areas.',
    'drainage': 'Request declogging from the city engineering office before the rainy season.',
    'flood': 'Request declogging from the city engineering office before the rainy season.',
    'streetlight': 'Report broken streetlights to the city electrical office and track the repair.',
    'light': 'Report broken streetlights to the city electrical office and track the repair.',
    'road': 'Request road repair from the city engineering office and place warning signs meanwhile.',
    'animal': 'Coordinate with the city veterinary office for stray animal control and remind pet owners of the leash rule.',
    'dog': 'Coordinate with the city veterinary office for stray animal control and remind pet owners of the leash rule.',
    'parking': 'Post no parking signs and assign tanod to clear obstructions on the reported streets.',
    'dispute': 'Schedule mediation through the Lupong Tagapamayapa.',
    'neighbor': 'Schedule mediation through the Lupong Tagapamayapa.',
    'security': 'Increase tanod visibility and coordinate with the police on the reported areas.',
    'peace': 'Increase tanod visibility and coordinate with the police on the reported areas.',
}
GENERIC_ACTION = ('Assign the responsible committee to inspect the most reported location and '
                  'discuss a preventive measure in the next barangay session.')


def recommended_action(category):
    if category.recommended_action:
        return category.recommended_action
    name = category.name.lower()
    for keyword, action in DEFAULT_ACTIONS.items():
        if keyword in name:
            return action
    return GENERIC_ACTION


def build_recommendations(days=30, limit=3, now=None):
    """Return the top complaint categories for the period with a suggested action each."""
    now = now or timezone.now()
    start = now - timedelta(days=days)
    previous_start = start - timedelta(days=days)
    base = Complaint.objects.filter(category__isnull=False)
    current = base.filter(created_at__gte=start, created_at__lte=now)
    total = current.count()
    if not total:
        return []

    previous_counts = dict(
        base.filter(created_at__gte=previous_start, created_at__lt=start)
        .values_list('category').annotate(n=Count('id')).values_list('category', 'n')
    )
    rows = (current.values('category').annotate(n=Count('id')).order_by('-n')[:limit])
    categories = Category.objects.in_bulk([row['category'] for row in rows])

    results = []
    for row in rows:
        category = categories[row['category']]
        count = row['n']
        before = previous_counts.get(category.id, 0)
        locations = Counter(
            loc.strip().lower() for loc in current.filter(category=category).values_list('location', flat=True) if loc
        )
        top_location = ''
        if locations:
            key, hits = locations.most_common(1)[0]
            original = current.filter(category=category, location__iexact=key).values_list('location', flat=True).first()
            top_location = f'{original} ({hits} reports)' if hits > 1 else original
        if before == 0:
            trend = 'new this period'
        elif count > before:
            trend = f'up from {before}'
        elif count < before:
            trend = f'down from {before}'
        else:
            trend = 'same as before'
        results.append({
            'category': category.name,
            'count': count,
            'share': round(count / total * 100),
            'previous': before,
            'trend': trend,
            'top_location': top_location,
            'action': recommended_action(category),
            'estimated_days': category.estimated_days,
        })
    return results


def satisfaction_summary(queryset=None):
    queryset = queryset if queryset is not None else Complaint.objects.all()
    rated = queryset.exclude(satisfaction='')
    total = rated.count()
    satisfied = rated.filter(satisfaction='satisfied').count()
    return {
        'rated': total,
        'satisfied': satisfied,
        'not_satisfied': total - satisfied,
        'rate': round(satisfied / total * 100) if total else None,
    }
