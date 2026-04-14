from django import forms
from .models import Change


class ChangeForm(forms.ModelForm):
    class Meta:
        model = Change
        fields = [
            'title', 'description', 'risk_level',
            'planned_date', 'rollback_plan',
            'affected_system', 'affected_system_other', 'notes',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'risk_level': forms.Select(attrs={'class': 'form-select'}),
            'planned_date': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'rollback_plan': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'affected_system': forms.Select(attrs={'class': 'form-select', 'id': 'id_affected_system'}),
            'affected_system_other': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Specify system…'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['planned_date'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['affected_system_other'].required = False
        self.fields['notes'].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('affected_system') == 'other' and not cleaned.get('affected_system_other'):
            self.add_error('affected_system_other', 'Please specify the system.')
        return cleaned
