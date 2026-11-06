from django import forms
from django.core.validators import EmailValidator
from django.core.exceptions import ValidationError
import re
from datetime import date

from .models import User

INPUT = 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500'


class RegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500',
            'placeholder': 'Enter password'
        }), 
        label='Password'
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500',
            'placeholder': 'Confirm password'
        }), 
        label='Confirm Password'
    )
    
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'birth_date', 'address', 'contact_number',
                  'valid_id_type', 'valid_id']
        labels = {
            'birth_date': 'Birthday',
            'address': 'Home Address in Barangay 10',
            'contact_number': 'Mobile Number (optional)',
            'valid_id_type': 'Type of Valid ID',
            'valid_id': 'Photo of Your Valid ID',
        }
        widgets = {
            'birth_date': forms.DateInput(attrs={'type': 'date', 'class': INPUT}),
            'address': forms.TextInput(attrs={'class': INPUT, 'placeholder': 'House no., street, Barangay 10, Caloocan City'}),
            'contact_number': forms.TextInput(attrs={'class': INPUT, 'placeholder': '09XX XXX XXXX'}),
            'valid_id_type': forms.Select(attrs={'class': INPUT}),
            'valid_id': forms.ClearableFileInput(attrs={'class': INPUT, 'accept': 'image/*'}),
            'email': forms.EmailInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500',
                'placeholder': 'your@email.com'
            }),
            'first_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500',
                'placeholder': 'First name'
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500',
                'placeholder': 'Last name'
            }),
        }
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        
        if User.objects.filter(email=email).exists():
            raise ValidationError('This email is already registered.')
        
        try:
            EmailValidator()(email)
        except ValidationError:
            raise ValidationError('Please enter a valid email address.')
        
        return email
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('birth_date', 'address', 'valid_id_type', 'valid_id'):
            self.fields[name].required = True
        self.fields['valid_id_type'].choices = [('', 'Select the ID you will upload')] + list(User.ID_TYPE_CHOICES)

    def clean_birth_date(self):
        birth_date = self.cleaned_data.get('birth_date')
        if birth_date:
            today = date.today()
            if birth_date >= today:
                raise ValidationError('Please enter your real birthday.')
            if today.year - birth_date.year > 120:
                raise ValidationError('Please check the year of your birthday.')
        return birth_date

    def clean_contact_number(self):
        number = re.sub(r'[\s-]', '', self.cleaned_data.get('contact_number') or '')
        if number and not re.fullmatch(r'(\+63|0)9\d{9}', number):
            raise ValidationError('Enter a mobile number like 09171234567.')
        return number

    def clean_valid_id(self):
        from complaints.image_utils import compress_image
        return compress_image(self.cleaned_data.get('valid_id'))

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        
        if password1 and password2 and password1 != password2:
            raise ValidationError("Passwords don't match")
        
        if len(password1) < 8:
            raise ValidationError("Password must be at least 8 characters long.")
        
        if not re.search(r'[A-Z]', password1):
            raise ValidationError("Password must contain at least one uppercase letter.")
        
        if not re.search(r'[0-9]', password1):
            raise ValidationError("Password must contain at least one number.")
        
        return password2
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        user.is_active = False  # User must verify email first
        user.is_verified = False
        user.residency_status = 'pending'  # barangay staff check the ID before complaints can be filed
        if commit:
            user.save()
        return user


class VerificationCodeForm(forms.Form):
    code = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg text-center text-2xl font-mono tracking-widest focus:outline-none focus:ring-2 focus:ring-orange-500',
            'placeholder': '000000',
            'maxlength': '6'
        })
    )