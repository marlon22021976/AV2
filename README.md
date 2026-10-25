# RUN_LAB — Personal Trainer & Athlete Platform

RUN_LAB é uma plataforma de gestão de treinos para personal trainers e atletas. Reúne prescrição individual e coletiva por equipes, acompanhamento em um dashboard web, um assistente conversacional de corrida e notificações de treino por e-mail e WhatsApp.

## Tecnologias utilizadas

- **Backend e API REST:** Python, Django, Django REST Framework e SQLite.
- **Interface e dashboard:** HTML5, CSS3 e JavaScript.
- **Assistente Coach IA:** API REST com respostas contextuais, quick replies e busca de preços de produtos.
- **Busca de preços:** `requests` e `beautifulsoup4`; quando a consulta externa não retorna resultados, o sistema pode exibir estimativas claramente identificadas.
- **RPA e notificações:** `pywhatkit` e `pyautogui` para automação do WhatsApp Web; SMTP autenticado do Gmail via `smtplib` para e-mails.
- **Dependências:** declaradas em [`requirements.txt`](./requirements.txt).

> **Observação sobre a interface:** o frontend atual usa HTML, CSS e JavaScript próprios. Não foi encontrada uma dependência ou referência a Bootstrap nos arquivos da interface.
>
> **Observação sobre e-mail:** o envio implementado usa `smtplib` diretamente. `django.core.mail` não é o mecanismo utilizado atualmente pelo módulo RPA.

## Os cinco pilares da AV2

1. **REST API e CRUD:** gerenciamento de atletas, equipes, treinos, produtos e logs de notificação. Os treinos incluem campos opcionais para repetições, ritmo de tiro e descanso.
2. **Django Admin avançado:** cadastro de atletas e equipes, organização dos campos do treino com `fieldsets` e seleção de membros da equipe com `filter_horizontal`. No formulário da equipe, é possível confirmar uma prescrição coletiva; ao salvar, o sistema cria um treino para cada atleta selecionado e enfileira os envios de e-mail e WhatsApp.
3. **RPA de WhatsApp:** envio de prescrições pelo WhatsApp Web usando `pywhatkit` e `pyautogui`, incluindo dados de treinos intervalados e o nome da equipe em prescrições coletivas.
4. **Coach IA:** chatbot com contexto do treino e sua data, quick replies para temas como aquecimento, alongamento, hidratação e produtos, além de consulta de preços.
5. **Dashboard web:** interface para login, consulta da planilha do atleta, acompanhamento do progresso e interação com o Coach IA.

## Requisitos

- Python compatível com as versões declaradas em `requirements.txt`.
- Navegador para acessar o dashboard e o Django Admin.
- Para envio de e-mail: conta Gmail e senha de app.
- Para envio por WhatsApp: sessão ativa do WhatsApp Web no navegador e uma sessão gráfica disponível para a automação.

## Como executar localmente

### 1. Clonar o repositório

```bash
git clone https://github.com/marlon22021976/AV2.git
cd AV2
```

### 2. Criar e ativar o ambiente virtual

```bash
python -m venv venv
```

No Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

No Linux/macOS:

```bash
source venv/bin/activate
```

### 3. Instalar as dependências

```bash
python -m pip install -r requirements.txt
```

### 4. Configurar credenciais (opcional)

Para habilitar os envios de e-mail pelo Gmail, configure `GMAIL_EMAIL` e `GMAIL_APP_PASSWORD`. Use uma senha de app do Gmail; não coloque credenciais no código nem as envie ao repositório.

No Windows PowerShell:

```powershell
$env:GMAIL_EMAIL = "seu-email@gmail.com"
$env:GMAIL_APP_PASSWORD = "sua-senha-de-app"
```

No Linux/macOS:

```bash
export GMAIL_EMAIL="seu-email@gmail.com"
export GMAIL_APP_PASSWORD="sua-senha-de-app"
```

As variáveis precisam estar definidas no mesmo ambiente em que o servidor Django for iniciado.

### 5. Aplicar as migrações

```bash
python manage.py migrate
```

### 6. Criar o superusuário

```bash
python manage.py createsuperuser
```

### 7. Iniciar o servidor de desenvolvimento

```bash
python manage.py runserver
```

### 8. Acessar

- **Dashboard:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Django Admin:** [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)

## Endpoints principais

As rotas da API ficam sob `/api/` e exigem autenticação por token, exceto quando indicado.

| Recurso | Endpoint |
| --- | --- |
| Login e obtenção de token | `POST /api/auth/token/` |
| Logout | `POST /api/auth/logout/` |
| Atletas | `/api/atletas/` |
| Equipes | `/api/equipes/` |
| Treinos | `/api/treinos/` |
| Enviar treino mais recente por e-mail e WhatsApp | `POST /api/treinos/enviar-ultimo/` |
| Produtos | `/api/produtos/` |
| Logs de notificação | `/api/notificacoes/` |
| Coach IA | `POST /api/chatbot/` |

Após o login, envie o token no cabeçalho das chamadas autenticadas:

```http
Authorization: Token <seu-token>
```

## Prescrição e notificações de equipe

No Admin, abra ou cadastre uma equipe, selecione os atletas e, na seção **Prescrição coletiva (opcional)**:

1. Marque a confirmação de criação e envio.
2. Informe data, tipo, distância e pace alvo; os detalhes de tiros e as observações são opcionais.
3. Salve a equipe.

O sistema cria um treino individual para cada atleta selecionado, mantendo os mesmos dados da prescrição coletiva, e agenda o envio personalizado por e-mail e WhatsApp. Os resultados de cada canal são registrados em `NotificacaoLog`. Sem marcar a confirmação, salvar a equipe não cria treinos nem inicia envios.

O envio do WhatsApp controla o navegador e requer uma sessão autenticada do WhatsApp Web e ambiente gráfico disponível. A automação pode falhar quando o navegador, a rede ou o serviço externo estiver indisponível.

## Testes e verificações

Execute a suíte da aplicação:

```bash
python manage.py test core.tests
```

Verifique a configuração do Django e a consistência das migrações:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
```

## Configuração para desenvolvimento e produção

O arquivo de configurações incluído está orientado a desenvolvimento. Antes de publicar o sistema, configure uma `DJANGO_SECRET_KEY` segura, desative `DEBUG`, restrinja `ALLOWED_HOSTS` e `CORS_ALLOW_ALL_ORIGINS`, proteja as credenciais e configure corretamente HTTPS e as demais opções de segurança do Django. Não use os valores de desenvolvimento em produção.
