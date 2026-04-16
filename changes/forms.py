from datetime import datetime

from django import forms

from .models import Change

# Half-hour time slots for the From/To dropdowns
_TIME_SLOTS = [('', '— Select —')] + [
    (f'{h:02d}:{m:02d}', f'{h:02d}:{m:02d}')
    for h in range(24)
    for m in (0, 30)
]


class ChangeForm(forms.ModelForm):
    # Explicit ChoiceFields so we control the available options
    planned_from = forms.ChoiceField(
        choices=_TIME_SLOTS,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    planned_to = forms.ChoiceField(
        choices=_TIME_SLOTS,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Change
        fields = [
            'title', 'description', 'risk_level',
            'planned_date', 'planned_from', 'planned_to', 'rollback_plan',
            'affected_system', 'affected_system_other',
            'affected_region', 'notes',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'risk_level': forms.Select(attrs={'class': 'form-select'}),
            'planned_date': forms.DateInput(
                attrs={'class': 'form-control', 'type': 'text', 'autocomplete': 'off'},
                format='%Y-%m-%d',
            ),
            'rollback_plan': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'affected_system': forms.Select(attrs={'class': 'form-select', 'id': 'id_affected_system'}),
            'affected_system_other': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Specify system…'}),
            'affected_region': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['planned_date'].input_formats = ['%Y-%m-%d']
        self.fields['affected_system_other'].required = False
        self.fields['notes'].required = False
        # Pre-select existing time values when editing
        if self.instance and self.instance.pk:
            if self.instance.planned_from:
                self.initial['planned_from'] = self.instance.planned_from.strftime('%H:%M')
            if self.instance.planned_to:
                self.initial['planned_to'] = self.instance.planned_to.strftime('%H:%M')

    def clean_planned_from(self):
        val = self.cleaned_data.get('planned_from')
        if not val:
            return None
        return datetime.strptime(val, '%H:%M').time()

    def clean_planned_to(self):
        val = self.cleaned_data.get('planned_to')
        if not val:
            return None
        return datetime.strptime(val, '%H:%M').time()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('affected_system') == 'other' and not cleaned.get('affected_system_other'):
            self.add_error('affected_system_other', 'Please specify the system.')
        return cleaned
