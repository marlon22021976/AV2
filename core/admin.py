from django import forms
from django.contrib import admin, messages
from django.db import transaction

from .models import Atleta, Equipe, NotificacaoLog, Produto, Treino


class EquipeAdminForm(forms.ModelForm):
    enviar_prescricao = forms.BooleanField(
        required=False,
        label='Criar treino coletivo e enviar por E-mail e WhatsApp ao salvar',
    )
    data = forms.DateField(
        required=False,
        label='Data',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    tipo = forms.ChoiceField(
        required=False,
        choices=[('', '---------'), *Treino.TIPO_CHOICES],
        label='Tipo',
    )
    distancia_km = forms.DecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        label='Distância (km)',
    )
    pace_alvo = forms.CharField(required=False, max_length=10, label='Pace alvo')
    repeticoes_tiro = forms.CharField(required=False, max_length=20, label='Repetições')
    pace_tiro = forms.CharField(required=False, max_length=20, label='Pace do tiro')
    tipo_descanso = forms.ChoiceField(
        required=False,
        choices=[('', '---------'), *Treino._meta.get_field('tipo_descanso').choices],
        label='Tipo de descanso',
    )
    pace_descanso = forms.CharField(required=False, max_length=20, label='Pace/tempo de descanso')
    observacoes = forms.CharField(required=False, widget=forms.Textarea, label='Observações')

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('enviar_prescricao'):
            required = ('data', 'tipo', 'distancia_km', 'pace_alvo')
            missing = [name for name in required if not cleaned_data.get(name)]
            if missing:
                for name in missing:
                    self.add_error(name, 'Obrigatório para criar e enviar a prescrição coletiva.')
        return cleaned_data

    class Meta:
        model = Equipe
        fields = ('nome', 'descricao', 'atletas')


@admin.register(Equipe)
class EquipeAdmin(admin.ModelAdmin):
    form = EquipeAdminForm
    filter_horizontal = ('atletas',)
    list_display = ('nome', 'quantidade_atletas')
    fieldsets = (
        ('Equipe', {'fields': ('nome', 'descricao', 'atletas')}),
        (
            'Prescrição coletiva (opcional)',
            {
                'description': (
                    'Marque a confirmação para criar um treino para cada atleta selecionado '
                    'e enfileirar o envio por E-mail e WhatsApp depois de salvar.'
                ),
                'fields': (
                    'enviar_prescricao',
                    'data',
                    'tipo',
                    'distancia_km',
                    'pace_alvo',
                    'repeticoes_tiro',
                    'pace_tiro',
                    'tipo_descanso',
                    'pace_descanso',
                    'observacoes',
                ),
            },
        ),
    )

    @admin.display(description='Atletas')
    def quantidade_atletas(self, equipe):
        return equipe.atletas.count()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not form.cleaned_data.get('enviar_prescricao'):
            return

        equipe = form.instance
        atletas = list(equipe.atletas.all())
        if not atletas:
            self.message_user(
                request,
                'A equipe foi salva, mas nenhum atleta foi selecionado para a prescrição.',
                level=messages.WARNING,
            )
            return

        treino_data = {
            field: form.cleaned_data.get(field) or None
            for field in (
                'repeticoes_tiro',
                'pace_tiro',
                'tipo_descanso',
                'pace_descanso',
                'observacoes',
            )
        }
        treino_data.update({
            field: form.cleaned_data[field]
            for field in ('data', 'tipo', 'distancia_km', 'pace_alvo')
        })
        with transaction.atomic():
            treinos = [
                Treino.objects.create(atleta=atleta, **treino_data)
                for atleta in atletas
            ]

        def enqueue_notifications():
            from .views import _dispatch_notifications, _format_workout_notification, _report_rpa_worker_error, rpa_executor

            for treino in treinos:
                subject, summary = _format_workout_notification(
                    treino.atleta,
                    treino,
                    equipe=equipe,
                )
                future = rpa_executor.submit(
                    _dispatch_notifications,
                    treino.atleta_id,
                    treino.atleta.email,
                    treino.atleta.whatsapp,
                    subject,
                    summary,
                )
                future.add_done_callback(_report_rpa_worker_error)

        transaction.on_commit(enqueue_notifications)
        self.message_user(
            request,
            (
                f'Treino salvo para {len(treinos)} atletas; os envios de E-mail e '
                'WhatsApp foram enfileirados após a confirmação do salvamento.'
            ),
            level=messages.SUCCESS,
        )


@admin.register(Treino)
class TreinoAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Informações Gerais', {
            'fields': (
                'atleta',
                'data',
                'status',
                'tipo',
                'distancia_km',
                'pace_alvo',
            ),
        }),
        ('Detalhamento de Intervalado / Tiros', {
            'fields': (
                'repeticoes_tiro',
                'pace_tiro',
                'tipo_descanso',
                'pace_descanso',
            ),
        }),
        ('Observações', {
            'fields': ('observacoes',),
        }),
    )


admin.site.register(Atleta)
admin.site.register(Produto)
admin.site.register(NotificacaoLog)
