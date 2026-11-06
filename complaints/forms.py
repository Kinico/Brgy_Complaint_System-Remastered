from django import forms
from .models import Complaint, Category
from .image_utils import compress_image

FIELD = 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500'

COMMON_FIELDS = ['category', 'location', 'description', 'incident_date', 'respondent_name', 'image', 'attachment']
COMMON_LABELS = {
    'incident_date': 'When did it happen? (optional)',
    'respondent_name': 'Person or establishment involved (optional)',
    'image': 'Photo (optional)',
    'attachment': 'Video or audio recording (optional, up to 25 MB)',
}
COMMON_WIDGETS = {
    'description': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Describe your complaint in detail...', 'class': FIELD}),
    'location': forms.TextInput(attrs={'placeholder': 'e.g., Block 5, Lot 12, Barangay 10', 'class': FIELD}),
    'category': forms.Select(attrs={'class': FIELD}),
    'incident_date': forms.DateInput(attrs={'type': 'date', 'class': FIELD}),
    'respondent_name': forms.TextInput(attrs={'placeholder': 'e.g., owner of the videoke bar on Street 4', 'class': FIELD}),
    'image': forms.ClearableFileInput(attrs={'accept': 'image/*', 'class': FIELD}),
    'attachment': forms.ClearableFileInput(attrs={'accept': 'video/*,audio/*', 'class': FIELD}),
}


class _ComplaintFormMixin:
    def setup_category(self):
        self.fields['category'].queryset = Category.objects.all()
        self.fields['category'].empty_label = "Select a category"

    def clean_image(self):
        return compress_image(self.cleaned_data.get('image'))

    def clean_incident_date(self):
        from django.utils import timezone
        value = self.cleaned_data.get('incident_date')
        if value and value > timezone.localdate():
            raise forms.ValidationError('The incident date cannot be in the future.')
        return value


class ComplaintForm(_ComplaintFormMixin, forms.ModelForm):
    class Meta:
        model = Complaint
        fields = COMMON_FIELDS
        labels = COMMON_LABELS
        widgets = COMMON_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_category()
        self.fields['category'].required = True


class WalkInComplaintForm(_ComplaintFormMixin, forms.ModelForm):
    """Used by barangay staff to encode a complaint brought to the hall in person,
    for example during an emergency or when the resident has no internet connection."""
    resident_email = forms.EmailField(
        required=False, label='Registered email of the complainant (optional)',
        help_text='If the complainant has an account, the complaint will also appear in their My Complaints page.',
        widget=forms.EmailInput(attrs={'class': FIELD, 'placeholder': 'resident@email.com'}))

    class Meta:
        model = Complaint
        fields = ['complainant_name', 'complainant_contact'] + COMMON_FIELDS
        labels = dict(COMMON_LABELS, complainant_name='Complainant name', complainant_contact='Contact number or address')
        widgets = dict(COMMON_WIDGETS,
                       complainant_name=forms.TextInput(attrs={'class': FIELD, 'placeholder': 'Full name of the complainant'}),
                       complainant_contact=forms.TextInput(attrs={'class': FIELD, 'placeholder': 'Mobile number or house address'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_category()
        self.fields['category'].required = True
        self.fields['complainant_name'].required = True

    def clean_resident_email(self):
        from accounts.models import User
        email = (self.cleaned_data.get('resident_email') or '').strip()
        if not email:
            return None
        user = User.objects.filter(email__iexact=email, role='resident').first()
        if not user:
            raise forms.ValidationError('No resident account uses this email. Leave it blank to encode without an account.')
        return user


class SatisfactionForm(forms.Form):
    satisfaction = forms.ChoiceField(choices=Complaint.SATISFACTION_CHOICES, widget=forms.RadioSelect)
    satisfaction_comment = forms.CharField(
        required=False, max_length=1000, label='Comment (optional)',
        widget=forms.Textarea(attrs={'rows': 3, 'class': FIELD, 'placeholder': 'Tell the barangay how it went'}))
