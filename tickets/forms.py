from django import forms
from .models import Ticket, TicketComment, TicketAttachment


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['title', 'description', 'assignee', 'requester_email', 'requester_name']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from users.models import User
        self.fields['assignee'].queryset = User.objects.filter(is_admin=True, is_active=True)
        self.fields['assignee'].empty_label = '— Unassigned —'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class TicketUpdateForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['title', 'status', 'assignee']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from users.models import User
        self.fields['assignee'].queryset = User.objects.filter(is_admin=True, is_active=True)
        self.fields['assignee'].empty_label = '— Unassigned —'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class CommentForm(forms.ModelForm):
    class Meta:
        model = TicketComment
        fields = ['body']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 4, 'class': 'form-control', 'placeholder': 'Write a comment...', 'dir': 'auto'}),
        }


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = TicketAttachment
        fields = ['file']


class PortalTicketForm(forms.ModelForm):
    """Minimal ticket form for the employee portal — no assignee, no requester fields."""
    class Meta:
        model = Ticket
        fields = ['title', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Describe the issue in as much detail as possible.', 'dir': 'auto'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].widget.attrs['placeholder'] = 'Brief summary of your issue'
        self.fields['title'].widget.attrs['dir'] = 'auto'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
