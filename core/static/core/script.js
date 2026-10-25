(() => {
    const state = {
        token: sessionStorage.getItem('runlab-token'),
        athlete: null,
        workouts: [],
        products: [],
        isStaff: false,
    };
    const loginScreen = document.querySelector('#login-screen');
    const appShell = document.querySelector('#app-shell');
    const loginError = document.querySelector('#login-error');
    const workoutList = document.querySelector('#workout-list');
    const sendFeedback = document.querySelector('#send-feedback');
    const messages = document.querySelector('#chat-messages');
    let chatContext = null;

    const theme = localStorage.getItem('runlab-theme') || 'light';
    document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light';
    const themeButtons = document.querySelectorAll('.theme-toggle');

    function updateThemeButtons() {
        const isLight = document.documentElement.dataset.theme === 'light';
        document.querySelector('meta[name="theme-color"]').content = isLight ? '#f3f5f1' : '#0d0f12';
        themeButtons.forEach((button) => {
            const label = isLight ? 'Escuro' : 'Claro';
            button.setAttribute('aria-label', `Ativar modo ${isLight ? 'escuro' : 'claro'}`);
            button.title = `Ativar modo ${isLight ? 'escuro' : 'claro'}`;
            button.querySelector('[aria-hidden="true"]').textContent = isLight ? '☾' : '☀';
            button.querySelector('.theme-toggle-label').textContent = label;
        });
    }

    themeButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const nextTheme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
            document.documentElement.dataset.theme = nextTheme;
            localStorage.setItem('runlab-theme', nextTheme);
            updateThemeButtons();
        });
    });
    updateThemeButtons();

    document.querySelector('#current-date').textContent = new Intl.DateTimeFormat('pt-BR', {
        weekday: 'short',
        day: '2-digit',
        month: 'short',
    }).format(new Date()).toUpperCase();

    const revealObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
        });
    }, { threshold: 0.12 });
    document.querySelectorAll('.reveal').forEach((element, index) => {
        element.style.transitionDelay = `${Math.min(index % 4, 3) * 70}ms`;
        revealObserver.observe(element);
    });

    async function apiFetch(path, options = {}) {
        const headers = new Headers(options.headers || {});
        if (state.token) headers.set('Authorization', `Token ${state.token}`);
        if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
        const response = await fetch(path, { ...options, headers });
        const payload = response.status === 204 ? null : await response.json().catch(() => null);
        if (!response.ok) {
            const detail = payload?.detail || Object.entries(payload || {})
                .map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
                .join(' ') || `Erro HTTP ${response.status}`;
            if (response.status === 401) logout(false);
            throw new Error(detail);
        }
        return payload;
    }

    function showLogin(error = '') {
        loginScreen.hidden = false;
        appShell.hidden = true;
        loginError.textContent = error;
        loginError.hidden = !error;
    }

    function showDashboard() {
        loginScreen.hidden = true;
        appShell.hidden = false;
    }

    function clearChatHistory() {
        chatContext = null;
        messages.replaceChildren();
        sessionStorage.removeItem('runlab-chat-history');
        sessionStorage.removeItem('runlab-chat-context');
        sessionStorage.removeItem('runlab-chat-owner');
    }

    async function resetChat() {
        clearChatHistory();
        const typing = createElement('div', 'message-row coach-message', '');
        typing.append(createElement('div', 'message-avatar', '✳'));
        const typingBubble = createElement('div', 'message-bubble typing-indicator', '');
        for (let index = 0; index < 3; index += 1) typingBubble.append(createElement('i', '', ''));
        typing.append(typingBubble);
        messages.append(typing);
        try {
            const result = await apiFetch('/api/chatbot/', {
                method: 'POST',
                body: JSON.stringify({ mensagem: 'iniciar', reset: true }),
            });
            typing.remove();
            addMessage(result.mensagem || result.resposta, 'coach', result.precos || [], result.opcoes || []);
            chatContext = result.context || null;
            const owner = state.athlete.login || String(state.athlete.id);
            sessionStorage.setItem('runlab-chat-owner', owner);
        } catch (error) {
            typing.remove();
            addMessage(`Não consegui iniciar o Coach IA: ${error.message}`, 'coach');
        }
    }

    async function login(username, password) {
        const response = await fetch('/api/auth/token/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.non_field_errors?.[0] || payload.detail || 'Usuário ou senha inválidos.');
        state.token = payload.token;
        state.isStaff = Boolean(payload.is_staff);
        sessionStorage.setItem('runlab-token', state.token);
        clearChatHistory();
        await loadDashboard();
    }

    async function loadDashboard() {
        try {
            const athletes = await apiFetch('/api/atletas/');
            if (!athletes.length) {
                throw new Error('Nenhum perfil de aluno está vinculado a este acesso. Peça ao personal para concluir o cadastro.');
            }
            state.athlete = state.isStaff ? athletes[0] : athletes[0];
            const chatOwner = state.athlete.login || String(state.athlete.id);
            if (sessionStorage.getItem('runlab-chat-owner') !== chatOwner) clearChatHistory();
            const athleteQuery = state.isStaff ? `?atleta=${encodeURIComponent(state.athlete.id)}` : '';
            const [workouts, products] = await Promise.all([
                apiFetch(`/api/treinos/${athleteQuery}`),
                apiFetch(`/api/produtos/${athleteQuery}`),
            ]);
            state.workouts = workouts;
            state.products = products;
            renderDashboard();
            showDashboard();
            await resetChat();
        } catch (error) {
            if (state.token) showLogin(error.message);
        }
    }

    document.querySelector('#login-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        loginError.hidden = true;
        const form = new FormData(event.currentTarget);
        try {
            await login(form.get('username').trim(), form.get('password'));
        } catch (error) {
            showLogin(error.message);
        }
    });

    async function logout(callEndpoint = true) {
        if (callEndpoint && state.token) {
            try {
                await fetch('/api/auth/logout/', {
                    method: 'POST',
                    headers: { Authorization: `Token ${state.token}` },
                });
            } catch (error) {
                console.error('Não foi possível invalidar o token no servidor.', error);
            }
        }
        state.token = null;
        sessionStorage.removeItem('runlab-token');
        clearChatHistory();
        showLogin();
    }
    document.querySelectorAll('#logout-button, #logout-button-top').forEach((button) => {
        button.addEventListener('click', () => logout());
    });

    function renderDashboard() {
        const athlete = state.athlete;
        const initials = athlete.nome.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
        document.querySelector('#sidebar-athlete-name').textContent = athlete.nome;
        document.querySelector('#sidebar-athlete-level').textContent = `Atleta · ${athlete.nivel_display || athlete.nivel}`;
        document.querySelector('.avatar-small').textContent = initials;
        document.querySelector('#top-avatar').textContent = initials;
        document.querySelector('#top-avatar').setAttribute('aria-label', `Perfil de ${athlete.nome}`);
        document.querySelector('#welcome-copy').textContent = `Consistência constrói velocidade. Bora manter o ritmo, ${athlete.nome.split(' ')[0]}.`;
        document.querySelector('#send-workout-button').hidden = state.isStaff;
        renderWorkouts();
        renderProducts();
        updateMetrics();
    }

    const typeLabels = {
        regenerativo: ['REGENERATIVO', 'type-easy'],
        continuo: ['CONTÍNUO', 'type-long'],
        intervalado: ['INTERVALADO / TIROS', 'type-tempo'],
        longao: ['LONGÃO', 'type-long'],
    };
    const statusLabels = { pendente: 'CONCLUIR', concluido: 'CONCLUÍDO', cancelado: 'CANCELADO' };

    function renderWorkouts() {
        const typeFilter = document.querySelector('#filter-type').value;
        const statusFilter = document.querySelector('#filter-status').value;
        const workouts = state.workouts.filter((workout) =>
            (!typeFilter || workout.tipo === typeFilter) && (!statusFilter || workout.status === statusFilter));
        document.querySelector('#workout-count').textContent = String(workouts.length).padStart(2, '0');
        document.querySelector('#nav-workout-count').textContent = String(state.workouts.length).padStart(2, '0');
        workoutList.replaceChildren();
        if (!workouts.length) {
            workoutList.append(createElement('p', 'empty-state', state.workouts.length ? 'Nenhum treino corresponde aos filtros.' : 'Seu personal ainda não cadastrou treinos.'));
            return;
        }
        workouts.forEach((workout) => {
            const row = createElement('article', `workout-item${workout.status === 'concluido' ? ' completed' : ''}`, '');
            row.dataset.workout = '';
            const date = new Date(`${workout.data}T12:00:00`);
            const dateCell = createElement('div', 'workout-date', '');
            dateCell.append(createElement('b', '', date.toLocaleDateString('pt-BR', { day: '2-digit' })));
            dateCell.append(createElement('span', '', date.toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', '').toUpperCase()));
            row.append(dateCell);

            const [typeLabel, typeClass] = typeLabels[workout.tipo] || [workout.tipo_display || workout.tipo, 'type-long'];
            const type = createElement('div', `workout-type ${typeClass}`, '');
            type.append(createElement('i', '', ''));
            type.append(document.createTextNode(` ${typeLabel}`));
            row.append(type);

            const info = createElement('div', 'workout-info', '');
            info.append(createElement('h3', '', workout.tipo_display || typeLabel));
            info.append(createElement('p', '', `${workout.distancia_km} km · ${workout.pace_alvo} · ${workout.status_display || workout.status}`));
            row.append(info);

            const meta = createElement('div', 'workout-meta', '');
            const distance = createElement('strong', '', '');
            distance.append(document.createTextNode(String(workout.distancia_km)));
            distance.append(createElement('small', '', ' KM'));
            meta.append(distance);
            meta.append(createElement('span', '', workout.data));
            row.append(meta);

            const button = createElement('button', 'complete-button', '');
            button.type = 'button';
            button.disabled = workout.status !== 'pendente';
            button.setAttribute('aria-label', `${statusLabels[workout.status] || workout.status} · ${typeLabel}`);
            button.append(createElement('span', '', workout.status === 'concluido' ? '✓' : ''));
            button.append(document.createTextNode(` ${statusLabels[workout.status] || workout.status.toUpperCase()}`));
            if (workout.status === 'pendente') {
                button.addEventListener('click', () => completeWorkout(workout, button));
            }
            row.append(button);
            workoutList.append(row);
        });
    }

    function createElement(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text) element.textContent = text;
        return element;
    }

    async function completeWorkout(workout, button) {
        button.disabled = true;
        try {
            const updated = await apiFetch(`/api/treinos/${workout.id}/concluir/`, { method: 'POST' });
            state.workouts = state.workouts.map((item) => item.id === updated.id ? updated : item);
            renderWorkouts();
            updateMetrics();
        } catch (error) {
            button.disabled = false;
            showFeedback(error.message, true);
        }
    }

    function parsePace(value) {
        const match = /^(\d{1,2}):([0-5]\d)/.exec(value || '');
        return match ? Number(match[1]) * 60 + Number(match[2]) : null;
    }

    function updateMetrics() {
        const distance = state.workouts.reduce((sum, workout) => sum + Number(workout.distancia_km), 0);
        const paces = state.workouts.map((workout) => parsePace(workout.pace_alvo)).filter((pace) => pace !== null);
        const averagePace = paces.length ? Math.round(paces.reduce((sum, pace) => sum + pace, 0) / paces.length) : null;
        const completed = state.workouts.filter((workout) => workout.status === 'concluido').length;
        const percentage = state.workouts.length ? Math.round(completed / state.workouts.length * 100) : 0;
        document.querySelector('#metric-distance').textContent = distance.toFixed(1).replace('.', ',');
        const totalPaceWeight = state.workouts.reduce((sum, workout) => {
            const pace = parsePace(workout.pace_alvo);
            return pace === null ? sum : sum + pace * Number(workout.distancia_km);
        }, 0);
        const paceDistance = state.workouts.reduce((sum, workout) =>
            parsePace(workout.pace_alvo) === null ? sum : sum + Number(workout.distancia_km), 0);
        const weightedPace = paceDistance ? Math.round(totalPaceWeight / paceDistance) : null;
        document.querySelector('#metric-pace').textContent = averagePace === null
            ? '—'
            : `${Math.floor(weightedPace / 60)}:${String(weightedPace % 60).padStart(2, '0')}`;
        document.querySelector('#metric-completed').textContent = completed;
        document.querySelector('#metric-calories').textContent = new Intl.NumberFormat('pt-BR').format(Math.round(distance * 62));
        document.querySelector('[data-week-progress]').textContent = percentage;
        document.querySelector('.weekly-ring').setAttribute('aria-label', `${percentage}% dos treinos concluídos`);
        document.querySelector('.ring-value').style.strokeDashoffset = `${301.6 * (1 - percentage / 100)}`;
        document.querySelector('.mini-progress span').style.width = `${percentage}%`;
        document.querySelector('[data-week-count]').textContent = `${completed} DE ${state.workouts.length} TREINOS`;
        document.querySelector('#volume-total').replaceChildren(
            document.createTextNode(distance.toFixed(1).replace('.', ',')),
            createElement('small', '', ' KM'),
        );
        renderProgressChart();
    }

    function renderProgressChart() {
        const totals = Array(7).fill(0);
        state.workouts.forEach((workout) => {
            const day = new Date(`${workout.data}T12:00:00`).getDay();
            totals[(day + 6) % 7] += Number(workout.distancia_km);
        });
        const scale = Math.max(20, Math.ceil(Math.max(...totals) / 5) * 5);
        const points = totals.map((value, index) => ({
            x: index * 60,
            y: 144 - (value / scale) * 120,
        }));
        const line = `M${points.map((point) => `${point.x} ${point.y}`).join(' L')}`;
        document.querySelector('.chart-line').setAttribute('d', line);
        document.querySelector('.chart-area').setAttribute(
            'd',
            `${line} L360 144 L0 144 Z`,
        );
        document.querySelector('.chart-point').setAttribute('cx', points[6].x);
        document.querySelector('.chart-point').setAttribute('cy', points[6].y);
        document.querySelectorAll('.chart-y-labels span').forEach((label, index) => {
            label.textContent = String(Math.round(scale * (4 - index) / 4));
        });
    }

    function renderProducts() {
        const productList = document.querySelector('#product-list');
        productList.replaceChildren();
        if (!state.products.length) {
            productList.append(createElement('p', 'empty-state', 'Nenhum produto recomendado pelo seu personal.'));
            return;
        }
        state.products.forEach((product) => {
            const card = createElement('article', 'product-item', '');
            card.append(createElement('span', 'product-category', product.categoria_display || product.categoria));
            card.append(createElement('h3', '', product.nome_produto));
            card.append(createElement('p', '', product.descricao || 'Sem descrição.'));
            card.append(createElement('strong', 'product-price', `R$ ${Number(product.preco).toFixed(2).replace('.', ',')}`));
            productList.append(card);
        });
    }

    document.querySelector('#filter-type').addEventListener('change', renderWorkouts);
    document.querySelector('#filter-status').addEventListener('change', renderWorkouts);

    function showFeedback(message, isError = false) {
        sendFeedback.textContent = message;
        sendFeedback.classList.toggle('is-error', isError);
        sendFeedback.hidden = false;
    }

    document.querySelector('#send-workout-button').addEventListener('click', async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        showFeedback('Enviando e-mail e iniciando o WhatsApp Web…');
        try {
            const result = await apiFetch('/api/treinos/enviar-ultimo/', {
                method: 'POST',
                body: JSON.stringify({}),
            });
            showFeedback(result.detail || 'Envio do treino iniciado em segundo plano.');
        } catch (error) {
            showFeedback(error.message, true);
        } finally {
            button.disabled = false;
        }
    });

    function addMessage(text, sender, prices = [], options = []) {
        if (sender === 'user') {
            messages.querySelectorAll('.chat-quick-replies').forEach((element) => element.remove());
        }
        const row = createElement('div', `message-row ${sender === 'user' ? 'user-message' : 'coach-message'}`, '');
        if (sender === 'coach') row.append(createElement('div', 'message-avatar', '✳'));
        const bubble = createElement('div', 'message-bubble', '');
        if (sender === 'coach') {
            appendFormattedText(bubble, text);
        } else {
            bubble.append(document.createTextNode(text));
        }
        if (prices.length) appendPriceResults(bubble, prices);
        bubble.append(createElement('span', '', new Intl.DateTimeFormat('pt-BR', {
            hour: '2-digit',
            minute: '2-digit',
        }).format(new Date())));
        row.append(bubble);
        messages.append(row);
        if (sender === 'coach' && options.length) {
            const quickReplies = createElement('div', 'chat-quick-replies', '');
            options.forEach((option) => {
                const button = createElement('button', '', option.label);
                button.type = 'button';
                button.addEventListener('click', () => sendChatMessage(option.payload, option.label));
                quickReplies.append(button);
            });
            messages.append(quickReplies);
        }
        messages.scrollTop = messages.scrollHeight;
    }

    function appendFormattedText(container, text) {
        const parts = text.split(/(\*\*[^*]+\*\*)/g);
        parts.forEach((part) => {
            if (part.startsWith('**') && part.endsWith('**')) {
                const strong = createElement('strong', '', part.slice(2, -2));
                container.append(strong);
            } else {
                container.append(document.createTextNode(part));
            }
        });
    }

    function appendPriceResults(container, prices) {
        const list = createElement('div', 'price-results', '');
        prices.forEach((price) => {
            const item = createElement('div', 'price-result', '');
            item.append(createElement('strong', '', `${price.produto} — ${price.loja}`));
            item.append(createElement('span', 'price-value', price.preco_formatado));
            try {
                const url = new URL(price.url);
                if (url.protocol === 'https:' || url.protocol === 'http:') {
                    const link = createElement('a', '', price.fonte === 'estimativa' ? 'Pesquisar na loja ↗' : 'Ver oferta ↗');
                    link.href = url.href;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    item.append(link);
                }
            } catch (error) {
                console.warn('Link de preço ignorado por ser inválido.', error);
            }
            list.append(item);
        });
        container.append(list);
    }

    async function sendChatMessage(message, displayText = message) {
        const text = displayText.trim();
        if (!text) return;
        addMessage(text, 'user');
        const typing = createElement('div', 'message-row coach-message', '');
        typing.append(createElement('div', 'message-avatar', '✳'));
        const typingBubble = createElement('div', 'message-bubble typing-indicator', '');
        for (let index = 0; index < 3; index += 1) typingBubble.append(createElement('i', '', ''));
        typing.append(typingBubble);
        messages.append(typing);
        messages.scrollTop = messages.scrollHeight;
        try {
            const result = await apiFetch('/api/chatbot/', {
                method: 'POST',
                body: JSON.stringify({
                    mensagem: text,
                    payload: displayText === message ? undefined : message,
                    context: chatContext,
                }),
            });
            await new Promise((resolve) => window.setTimeout(resolve, 450));
            typing.remove();
            addMessage(result.mensagem || result.resposta, 'coach', result.precos || [], result.opcoes || []);
            chatContext = result.context || null;
        } catch (error) {
            typing.remove();
            addMessage(`Não consegui consultar o assistente agora: ${error.message}`, 'coach');
        }
    }

    const launcher = document.querySelector('#chat-launcher');
    const chatPanel = document.querySelector('#chat-panel');
    const chatInput = document.querySelector('#chat-input');
    const setChatOpen = (open) => {
        chatPanel.hidden = !open;
        launcher.setAttribute('aria-expanded', String(open));
        if (open) chatInput.focus();
    };
    launcher.addEventListener('click', () => setChatOpen(chatPanel.hidden));
    document.querySelector('#chat-close').addEventListener('click', () => {
        setChatOpen(false);
        launcher.focus();
    });
    document.querySelector('#chat-form').addEventListener('submit', (event) => {
        event.preventDefault();
        const message = chatInput.value;
        chatInput.value = '';
        sendChatMessage(message);
    });
    if (state.token) loadDashboard();
    else showLogin();
})();
