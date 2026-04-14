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
        fields = ['title', 'status', 'assignee', 'description']
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


class CommentForm(forms.ModelForm):
    class Meta:
        model = TicketComment
        fields = ['body', 'is_internal']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 4, 'class': 'form-control', 'placeholder': 'Write a comment...'}),
        }
        labels = {
            'is_internal': 'Internal note (not visible to requester)',
        }


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = TicketAttachment
        fields = ['file']
