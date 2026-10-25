from django.contrib.auth import get_user_model
from django.db import models

class Atleta(models.Model):
    NIVEL_CHOICES = [
        ('iniciante', 'Iniciante'),
        ('intermediario', 'Intermediário'),
        ('avancado', 'Avançado'),
    ]

    nome = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    whatsapp = models.CharField(max_length=20, help_text="Formato: 5521999999999")
    nivel = models.CharField(max_length=20, choices=NIVEL_CHOICES, default='iniciante')
    objetivo = models.CharField(max_length=100, help_text="Ex: Maratona 42k, 10k em 50min")
    criado_em = models.DateTimeField(auto_now_add=True)
    usuario = models.OneToOneField(
        get_user_model(),
        on_delete=models.SET_NULL,
        related_name='perfil_atleta',
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.nome} ({self.get_nivel_display()})"


class Equipe(models.Model):
    nome = models.CharField(max_length=100)
    descricao = models.TextField(blank=True, null=True)
    atletas = models.ManyToManyField(Atleta, related_name='equipes')

    def __str__(self):
        return self.nome


class Treino(models.Model):
    TIPO_CHOICES = [
        ('regenerativo', 'Regenerativo'),
        ('continuo', 'Corrida Contínua / Z2'),
        ('intervalado', 'Intervalado / Tiros'),
        ('longao', 'Longão'),
    ]

    STATUS_CHOICES = [
        ('pendente', 'Pendente'),
        ('concluido', 'Concluído'),
        ('cancelado', 'Cancelado'),
    ]

    atleta = models.ForeignKey(Atleta, on_delete=models.CASCADE, related_name='treinos')
    data = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    distancia_km = models.DecimalField(decimal_places=2, max_digits=5, help_text="Ex: 8.50")
    pace_alvo = models.CharField(max_length=10, help_text="Ex: 5:30 min/km")
    repeticoes_tiro = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Ex: 8x 400m ou 10x 1min",
    )
    pace_tiro = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Ex: 4:15 min/km",
    )
    tipo_descanso = models.CharField(
        max_length=15,
        choices=[
            ('ativo', 'Ativo (Trote / Caminhada)'),
            ('passivo', 'Passivo (Parado)'),
        ],
        default='ativo',
        blank=True,
        null=True,
    )
    pace_descanso = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Ex: 6:30 min/km ou 1 min parado",
    )
    observacoes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pendente')

    def __str__(self):
        return f"{self.atleta.nome} - {self.get_tipo_display()} ({self.data})"


class Produto(models.Model):
    CATEGORIA_CHOICES = [
        ('suplemento', 'Suplemento'),
        ('nutricao', 'Nutrição'),
        ('equipamento', 'Equipamento'),
        ('outro', 'Outro'),
    ]

    atleta = models.ForeignKey(
        Atleta,
        on_delete=models.CASCADE,
        related_name='produtos',
        null=True,
        blank=True,
    )
    nome_produto = models.CharField(max_length=120)
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, default='suplemento')
    preco = models.DecimalField(max_digits=8, decimal_places=2)
    descricao = models.TextField(blank=True)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        if self.atleta_id:
            return f"{self.nome_produto} - {self.atleta.nome}"
        return f"{self.nome_produto} (Global)"


class NotificacaoLog(models.Model):
    CANAL_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('email', 'E-mail'),
    ]
    STATUS_CHOICES = [
        ('sucesso', 'Sucesso'),
        ('falha', 'Falha'),
    ]

    atleta = models.ForeignKey(Atleta, on_delete=models.CASCADE, related_name='notificacoes')
    canal = models.CharField(max_length=10, choices=CANAL_CHOICES)
    status_envio = models.CharField(max_length=10, choices=STATUS_CHOICES)
    mensagem = models.TextField()
    data_envio = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data_envio']

    def __str__(self):
        return f"{self.get_canal_display()} - {self.get_status_envio_display()} ({self.atleta.nome})"