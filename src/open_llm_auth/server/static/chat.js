// Configure marked to use highlight.js
marked.setOptions({
    highlight: function(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    },
    breaks: true
});

// ==================== NEON EFFECTS ====================
function initNeonEffects() {
    // Add floating particles
    const canvas = document.createElement('canvas');
    canvas.id = 'neon-particles';
    canvas.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 0;
        opacity: 0.6;
    `;
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    let particles = [];
    let animationId;

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    class Particle {
        constructor() {
            this.reset();
        }

        reset() {
            this.x = Math.random() * canvas.width;
            this.y = canvas.height + 10;
            this.size = Math.random() * 2 + 1;
            this.speedY = Math.random() * 1 + 0.5;
            this.speedX = (Math.random() - 0.5) * 0.5;
            this.life = 1;
            this.decay = Math.random() * 0.005 + 0.002;
            this.color = Math.random() > 0.5 ? '#00f0ff' : '#ff00ff';
            if (Math.random() > 0.7) this.color = '#00ff41';
        }

        update() {
            this.y -= this.speedY;
            this.x += this.speedX;
            this.life -= this.decay;

            if (this.life <= 0 || this.y < -10) {
                this.reset();
            }
        }

        draw() {
            ctx.save();
            ctx.globalAlpha = this.life * 0.5;
            ctx.shadowBlur = 15;
            ctx.shadowColor = this.color;
            ctx.fillStyle = this.color;
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
        }
    }

    for (let i = 0; i < 30; i++) {
        const p = new Particle();
        p.y = Math.random() * canvas.height;
        particles.push(p);
    }

    function animate() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        particles.forEach(p => {
            p.update();
            p.draw();
        });
        animationId = requestAnimationFrame(animate);
    }
    animate();

    // Scanline effect
    const scanlines = document.createElement('div');
    scanlines.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: repeating-linear-gradient(
            0deg,
            rgba(0, 240, 255, 0.03) 0px,
            transparent 1px,
            transparent 2px,
            rgba(0, 240, 255, 0.02) 2px
        );
        background-size: 100% 4px;
        pointer-events: none;
        z-index: 10000;
        animation: scanlineMove 10s linear infinite;
    `;
    document.body.appendChild(scanlines);

    // Add glitch animation style
    const style = document.createElement('style');
    style.textContent = `
        @keyframes scanlineMove {
            0% { transform: translateY(0); }
            100% { transform: translateY(4px); }
        }
        @keyframes glitch {
            0%, 90%, 100% { transform: translate(0); }
            92% { transform: translate(-2px, 2px); }
            94% { transform: translate(2px, -2px); }
            96% { transform: translate(-2px, -2px); }
            98% { transform: translate(2px, 2px); }
        }
        @keyframes pulseGlow {
            0%, 100% { box-shadow: 0 0 10px var(--neon-cyan), 0 0 20px var(--neon-cyan), 0 0 40px var(--neon-cyan); }
            50% { box-shadow: 0 0 20px var(--neon-cyan), 0 0 40px var(--neon-cyan), 0 0 60px var(--neon-cyan); }
        }
        @keyframes textFlicker {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.95; }
            52% { opacity: 0.7; }
            54% { opacity: 0.95; }
        }
    `;
    document.head.appendChild(style);

    // Glitch effect on headers
    setInterval(() => {
        const headers = document.querySelectorAll('.sidebar-header h2, .chat-header label');
        headers.forEach(h => {
            if (Math.random() > 0.95) {
                h.style.animation = 'glitch 0.3s ease';
                setTimeout(() => h.style.animation = '', 300);
            }
        });
    }, 2000);
}

// ==================== CHAT FUNCTIONALITY ====================

let currentChatId = null;
let chats = JSON.parse(localStorage.getItem('open_llm_auth_chats') || '{}');

function saveChats() {
    localStorage.setItem('open_llm_auth_chats', JSON.stringify(chats));
    renderChatList();
}

function initNewChat() {
    currentChatId = Date.now().toString();
    chats[currentChatId] = {
        title: 'New Chat',
        messages: [],
        updatedAt: Date.now()
    };
    saveChats();
    renderMessages();
}

function renderChatList() {
    const list = document.getElementById('chat-list');
    list.innerHTML = '';
    const sortedChats = Object.entries(chats).sort((a, b) => b[1].updatedAt - a[1].updatedAt);
    
    for (const [id, chat] of sortedChats) {
        const div = document.createElement('div');
        div.className = `chat-history-item ${id === currentChatId ? 'active' : ''}`;
        div.textContent = chat.title || 'New Chat';
        div.onclick = () => {
            currentChatId = id;
            renderChatList();
            renderMessages();
        };
        list.appendChild(div);
    }
}

function renderMessages() {
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';
    
    if (!currentChatId || !chats[currentChatId]) return;
    
    for (const msg of chats[currentChatId].messages) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${msg.role}`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        let contentHtml = '';
        if (msg.role === 'assistant' && msg.thinking) {
            contentHtml += `<div class="thinking-content"><b>Thinking Process:</b><br>${marked.parse(msg.thinking)}</div>`;
        }
        
        if (msg.content) {
            contentHtml += marked.parse(msg.content);
        } else if (msg.role === 'assistant' && !msg.content && msg.thinking) {
             // In case there is only thinking but no content yet
        }
        
        contentDiv.innerHTML = contentHtml;
        msgDiv.appendChild(contentDiv);
        container.appendChild(msgDiv);
    }
    
    container.scrollTop = container.scrollHeight;
}

function getAuthHeaders() {
    const token = document.getElementById('server-token')?.value?.trim();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function loadModels() {
    const select = document.getElementById('model-select');
    try {
        const response = await fetch('/v1/models', { headers: getAuthHeaders() });
        if (!response.ok) {
            if (response.status === 401) {
                select.innerHTML = '<option value="">Auth required - enter token</option>';
                return;
            }
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        const models = data.data || [];
        
        select.innerHTML = '';
        if (models.length === 0) {
            select.innerHTML = '<option value="">No models available</option>';
            return;
        }
        
        models.forEach(m => {
            const option = document.createElement('option');
            option.value = m.id;
            // Label showing provider and reasoning capability
            option.textContent = m.id + (m.reasoning ? ' (Reasoning)' : '');
            select.appendChild(option);
        });
        
        // Load default model from config if possible
        try {
            const cfgResponse = await fetch('/config', { headers: getAuthHeaders() });
            const cfgData = await cfgResponse.json();
            if (cfgData.defaultModel) {
                select.value = cfgData.defaultModel;
            }
        } catch (e) {
            // ignore
        }
        
    } catch (err) {
        select.innerHTML = '<option value="">Error loading models</option>';
    }
}

document.getElementById('new-chat-btn').onclick = initNewChat;

const input = document.getElementById('chat-input');
input.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = (this.scrollHeight) + 'px';
});

input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('chat-form').dispatchEvent(new Event('submit'));
    }
});

document.getElementById('chat-form').onsubmit = async (e) => {
    e.preventDefault();
    if (!currentChatId) initNewChat();
    
    const text = input.value.trim();
    if (!text) return;
    
    const model = document.getElementById('model-select').value;
    if (!model) {
        alert('Please select a model');
        return;
    }
    
    input.value = '';
    input.style.height = 'auto';
    
    // Add user message
    chats[currentChatId].messages.push({ role: 'user', content: text });
    if (chats[currentChatId].messages.length === 1) {
        chats[currentChatId].title = text.substring(0, 30) + (text.length > 30 ? '...' : '');
    }
    chats[currentChatId].updatedAt = Date.now();
    saveChats();
    renderMessages();
    
    const thinkingMode = document.getElementById('thinking-mode-toggle').checked;
    
    // Create placeholder for assistant response
    const container = document.getElementById('chat-messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant';
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = '<div class="typing-indicator">Assistant is typing...</div>';
    msgDiv.appendChild(contentDiv);
    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
    
    const apiMessages = chats[currentChatId].messages.map(m => ({ role: m.role, content: m.content }));
    
    const payload = {
        model: model,
        messages: apiMessages,
        stream: true
    };
    
    // Some providers use `reasoning_effort` for thinking models (e.g. OpenAI o-series style)
    if (thinkingMode) {
        payload.reasoning_effort = "high";
    }
    
    try {
        const headers = { 'Content-Type': 'application/json', ...getAuthHeaders() };
        const response = await fetch('/v1/chat/completions', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) {
            let errMsg = 'Request failed';
            try {
                const err = await response.json();
                errMsg = err.error?.message || errMsg;
            } catch(e) {}
            throw new Error(errMsg);
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        
        let assistantContent = '';
        let assistantThinking = '';
        
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ') && line !== 'data: [DONE]') {
                    try {
                        const data = JSON.parse(line.substring(6));
                        const delta = data.choices[0]?.delta || {};
                        
                        // Fallback handle reasoning for DeepSeek / Kimi / etc.
                        if (delta.reasoning_content) {
                            assistantThinking += delta.reasoning_content;
                        }
                        
                        if (delta.content) {
                            assistantContent += delta.content;
                        }
                        
                        let html = '';
                        if (assistantThinking) {
                            html += `<div class="thinking-content"><b>Thinking Process:</b><br>${marked.parse(assistantThinking)}</div>`;
                        }
                        if (assistantContent) {
                            html += marked.parse(assistantContent);
                        } else if (!assistantContent && assistantThinking) {
                            html += '<div class="typing-indicator">Thinking...</div>';
                        } else if (!assistantContent && !assistantThinking) {
                            html += '<div class="typing-indicator">Assistant is typing...</div>';
                        }
                        
                        contentDiv.innerHTML = html;
                        container.scrollTop = container.scrollHeight;
                        
                    } catch (e) {
                        // ignore parse errors for partial chunks
                    }
                }
            }
        }
        
        chats[currentChatId].messages.push({
            role: 'assistant',
            content: assistantContent,
            thinking: assistantThinking
        });
        chats[currentChatId].updatedAt = Date.now();
        saveChats();
        
    } catch (err) {
        contentDiv.innerHTML = `<p style="color:red">Error: ${err.message}</p>`;
        // Optionally save the error message
    }
};

// Token input listener to reload models when token changes
document.getElementById('server-token')?.addEventListener('change', () => {
    loadModels();
});

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    initNeonEffects();
    loadModels();
    renderChatList();
    if (Object.keys(chats).length === 0) {
        initNewChat();
    } else {
        currentChatId = Object.keys(chats).sort((a, b) => chats[b].updatedAt - chats[a].updatedAt)[0];
        renderChatList();
        renderMessages();
    }
});
