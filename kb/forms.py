from django import forms
from .models import KBArticle


class KBArticleForm(forms.ModelForm):
    class Meta:
        model = KBArticle
        fields = ['title', 'subcategory', 'ticket_item', 'body', 'status']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'subcategory': forms.Select(attrs={'class': 'form-select', 'id': 'id_subcategory'}),
            'ticket_item': forms.Select(attrs={'class': 'form-select', 'id': 'id_ticket_item'}),
            'body': forms.Textarea(attrs={'class': 'form-control font-monospace', 'rows': 14}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from tickets.models import TicketSubCategory, TicketItem
        self.fields['subcategory'].queryset = (
            TicketSubCategory.objects
            .select_related('category')
            .exclude(category__name='HR')
            .order_by('category__name', 'name')
        )
        self.fields['subcategory'].empty_label = 'Select subcategory…'
        self.fields['subcategory'].required = False
        self.fields['ticket_item'].empty_label = 'Select item (optional)…'
        self.fields['ticket_item'].required = False
        if self.instance.pk and self.instance.subcategory_id:
            self.fields['ticket_item'].queryset = TicketItem.objects.filter(
                subcategory_id=self.instance.subcategory_id
            )
        else:
            self.fields['ticket_item'].queryset = TicketItem.objects.none()
