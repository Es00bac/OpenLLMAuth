
// API client
const API = {
    async request(method, path, body = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
            },
        };
        if (body) {
            options.body = JSON.stringify(body);
        }
        const response = await fetch(path, options);
        if (!response.ok) {
            const error = await response.json().catch(() => ({ error: 'Unknown error' }));
            throw new Error(error.detail || error.error || `HTTP ${response.status}`);
        }
        return response.json();
    },

    getConfig: () => API.request('GET', '/config'),
    saveConfig: (config) => API.request('POST', '/config', config),
    getProviders: () => API.request('GET', '/config/providers'),
    saveProvider: (id, provider) => API.request('PUT', `/config/providers/${id}`, provider),
    deleteProvider: (id) => API.request('DELETE', `/config/providers/${id}`),
    getAuthProfiles: () => API.request('GET', '/config/auth-profiles'),
    saveAuthProfile: (id, profile) => API.request('PUT', `/config/auth-profiles/${id}`, profile),
    deleteAuthProfile: (id) => API.request('DELETE', `/config/auth-profiles/${id}`),
    getModels: () => API.request('GET', '/v1/models'),
    getBuiltinProviders: () => API.request('GET', '/config/builtin-providers'),
    getConfiguredProviders: () => API.request('GET', '/config/configured-providers'),
    getProviderModels: (providerId) => API.request('GET', `/config/providers/${providerId}/models`),
};

// Toast notifications
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Modal handling
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.add('active');
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.remove('active');
}

// Close modals on escape or click outside
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal.active').forEach(m => m.classList.remove('active'));
    }
});

document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.remove('active');
        }
    });
});

document.querySelectorAll('.modal-close, .modal-cancel').forEach(btn => {
    btn.addEventListener('click', () => {
        btn.closest('.modal').classList.remove('active');
    });
});

// Tabs
let currentTab = 'providers';

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        currentTab = tab;
        
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        
        btn.classList.add('active');
        document.getElementById(`${tab}-tab`).classList.add('active');
        
        // Load data for the tab
        if (tab === 'providers') loadProviders();
        if (tab === 'auth') loadAuthProfiles();
        if (tab === 'models') loadModels();
        if (tab === 'settings') loadSettings();
    });
});

// Provider icon mapping
const PROVIDER_ICONS = {
    'openai': 'GPT',
    'anthropic': 'Claude',
    'google': 'Gemini',
    'github-copilot': 'Copilot',
    'amazon-bedrock': 'AWS',
    'mistral': 'Mistral',
    'groq': 'Groq',
    'together': 'Together',
    'openrouter': 'Router',
    'ollama': 'Ollama',
    'vllm': 'vLLM',
    'huggingface': 'HF',
    'azure': 'Azure',
    'cohere': 'Cohere',
    'ai21': 'AI21',
};

function getProviderIcon(providerId) {
    const normalized = providerId.toLowerCase().replace(/[-_]/g, '');
    for (const [key, icon] of Object.entries(PROVIDER_ICONS)) {
        if (normalized.includes(key.replace(/[-_]/g, ''))) {
            return icon;
        }
    }
    return providerId.slice(0, 2).toUpperCase();
}

// Load providers
async function loadProviders() {
    const list = document.getElementById('providers-list');
    try {
        const [config, builtin] = await Promise.all([
            API.getConfig(),
            API.getBuiltinProviders(),
        ]);
        
        const providers = [];
        
        // Add custom providers from config
        for (const [id, cfg] of Object.entries(config.providers || {})) {
            providers.push({
                id,
                ...cfg,
                isBuiltin: false,
            });
        }
        
        // Add builtin providers
        for (const [id, cfg] of Object.entries(builtin)) {
            if (!providers.find(p => p.id === id)) {
                providers.push({
                    id,
                    ...cfg,
                    isBuiltin: true,
                });
            }
        }
        
        if (providers.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🔌</div>
                    <p>No providers configured</p>
                    <button class="btn btn-primary mt-2" onclick="openAddProviderModal()">Add Provider</button>
                </div>
            `;
            return;
        }
        
        list.innerHTML = providers.map(p => `
            <div class="card" data-provider="${p.id}">
                <div class="card-icon">${getProviderIcon(p.id)}</div>
                <div class="card-content">
                    <div class="card-title">${p.id}</div>
                    <div class="card-subtitle">${p.baseUrl || 'No base URL'}</div>
                    <div class="card-meta">
                        <span>API: ${p.api || 'Not set'}</span>
                        <span>Auth: ${p.auth || 'api-key'}</span>
                        ${p.isBuiltin ? '<span class="status-badge active">Built-in</span>' : '<span class="status-badge">Custom</span>'}
                    </div>
                </div>
                <div class="card-actions">
                    ${!p.isBuiltin ? `<button class="btn btn-icon" onclick="editProvider('${p.id}')">✏️</button>` : ''}
                    ${!p.isBuiltin ? `<button class="btn btn-icon" onclick="deleteProvider('${p.id}')">🗑️</button>` : ''}
                </div>
            </div>
        `).join('');
    } catch (err) {
        showToast(`Failed to load providers: ${err.message}`, 'error');
        list.innerHTML = '<div class="empty-state">Failed to load providers</div>';
    }
}

// Load auth profiles
async function loadAuthProfiles() {
    const list = document.getElementById('auth-list');
    try {
        const profiles = await API.getAuthProfiles();
        
        if (profiles.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🔐</div>
                    <p>No authentication profiles</p>
                    <button class="btn btn-primary mt-2" onclick="openAddAuthModal()">Add Profile</button>
                </div>
            `;
            return;
        }
        
        list.innerHTML = profiles.map(p => {
            const hasSecret = p.type === 'api_key' ? p.key : p.type === 'token' ? p.token : p.access;
            const isExpired = p.expires && p.expires < Date.now();
            
            return `
                <div class="card" data-profile="${p.id}">
                    <div class="card-icon">🔐</div>
                    <div class="card-content">
                        <div class="card-title">${p.id}</div>
                        <div class="card-subtitle">${p.provider}</div>
                        <div class="card-meta">
                            <span>Type: ${p.type}</span>
                            ${hasSecret ? '<span class="status-badge active">Configured</span>' : '<span class="status-badge inactive">No secret</span>'}
                            ${isExpired ? '<span class="status-badge expired">Expired</span>' : ''}
                            ${p.email ? `<span>Email: ${p.email}</span>` : ''}
                        </div>
                    </div>
                    <div class="card-actions">
                        <button class="btn btn-icon" onclick="editAuthProfile('${p.id}')">✏️</button>
                        <button class="btn btn-icon" onclick="deleteAuthProfile('${p.id}')">🗑️</button>
                    </div>
                </div>
            `;
        }).join('');
    } catch (err) {
        showToast(`Failed to load auth profiles: ${err.message}`, 'error');
        list.innerHTML = '<div class="empty-state">Failed to load auth profiles</div>';
    }
}

// Load models
async function loadModels() {
    const list = document.getElementById('models-list');
    const countEl = document.getElementById('model-count');
    
    try {
        const data = await API.getModels();
        const models = data.data || [];
        
        countEl.textContent = `${models.length} models`;
        
        if (models.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🤖</div>
                    <p>No models available</p>
                </div>
            `;
            return;
        }
        
        list.innerHTML = models.map(m => {
            const provider = m.id.split('/')[0] || 'unknown';
            const modelName = m.id.split('/').slice(1).join('/') || m.id;
            
            return `
                <div class="model-card">
                    <div class="card-title">
                        ${modelName}
                        ${m.reasoning ? '<span class="model-badge reasoning">Reasoning</span>' : ''}
                    </div>
                    <div class="card-subtitle">${provider}</div>
                    <div class="model-capabilities">
                        ${(m.input || ['text']).map(cap => `<span class="capability">${cap}</span>`).join('')}
                        ${m.contextWindow ? `<span class="capability">${(m.contextWindow / 1000).toFixed(0)}k ctx</span>` : ''}
                    </div>
                </div>
            `;
        }).join('');
    } catch (err) {
        showToast(`Failed to load models: ${err.message}`, 'error');
        list.innerHTML = '<div class="empty-state">Failed to load models</div>';
    }
}

// Load settings
async function loadSettings() {
    try {
        const config = await API.getConfig();
        const providers = await API.getConfiguredProviders();
        
        const providerSelect = document.getElementById('default-provider');
        const modelSelect = document.getElementById('default-model');
        const modelGroup = document.getElementById('model-select-group');
        const preview = document.getElementById('default-model-preview');
        
        // Reset
        providerSelect.innerHTML = '<option value="">Select a configured provider...</option>';
        modelSelect.innerHTML = '<option value="">Select a provider first...</option>';
        modelGroup.classList.add('hidden');
        preview.classList.add('hidden');
        
        if (providers.length === 0) {
            providerSelect.innerHTML = '<option value="">No configured providers found</option>';
            return;
        }
        
        // Parse current default model to get provider
        const currentDefault = config.defaultModel || '';
        const currentProvider = currentDefault.split('/')[0] || '';
        const currentModel = currentDefault.split('/')[1] || '';
        
        // Populate provider dropdown
        const providerOptions = providers.map(p => 
            `<option value="${p.id}" ${p.id === currentProvider ? 'selected' : ''}>${p.id} (${p.authProfileCount} profile${p.authProfileCount !== 1 ? 's' : ''})</option>`
        ).join('');
        providerSelect.innerHTML += providerOptions;
        
        // If we have a current provider, load its models
        if (currentProvider && providers.find(p => p.id === currentProvider)) {
            await loadProviderModels(currentProvider, currentModel);
        }
        
    } catch (err) {
        showToast(`Failed to load settings: ${err.message}`, 'error');
    }
}

// Load models for a specific provider
async function loadProviderModels(providerId, selectedModel = '') {
    const modelSelect = document.getElementById('default-model');
    const modelGroup = document.getElementById('model-select-group');
    const preview = document.getElementById('default-model-preview');
    
    try {
        modelSelect.innerHTML = '<option value="">Loading models...</option>';
        modelGroup.classList.remove('hidden');
        
        const models = await API.getProviderModels(providerId);
        
        if (models.length === 0) {
            modelSelect.innerHTML = '<option value="">No models found for this provider</option>';
            preview.classList.add('hidden');
            return;
        }
        
        const modelOptions = models.map(m => {
            const modelId = m.id;
            const fullRef = `${providerId}/${modelId}`;
            const isSelected = modelId === selectedModel || fullRef === selectedModel;
            return `<option value="${modelId}" ${isSelected ? 'selected' : ''}>${m.name || modelId}</option>`;
        }).join('');
        
        modelSelect.innerHTML = `<option value="">Select a model...</option>${modelOptions}`;
        
        // Show preview if model selected
        if (selectedModel) {
            updateModelPreview();
        }
        
    } catch (err) {
        showToast(`Failed to load models: ${err.message}`, 'error');
        modelSelect.innerHTML = '<option value="">Error loading models</option>';
    }
}

// Update the model preview
function updateModelPreview() {
    const provider = document.getElementById('default-provider').value;
    const model = document.getElementById('default-model').value;
    const preview = document.getElementById('default-model-preview');
    const fullRefEl = document.getElementById('full-model-ref');
    
    if (provider && model) {
        const fullRef = `${provider}/${model}`;
        fullRefEl.textContent = fullRef;
        preview.classList.remove('hidden');
    } else {
        preview.classList.add('hidden');
    }
}

// Provider selection change handler
document.getElementById('default-provider').addEventListener('change', async (e) => {
    const providerId = e.target.value;
    const modelGroup = document.getElementById('model-select-group');
    const preview = document.getElementById('default-model-preview');
    
    if (!providerId) {
        modelGroup.classList.add('hidden');
        preview.classList.add('hidden');
        return;
    }
    
    await loadProviderModels(providerId);
});

// Model selection change handler
document.getElementById('default-model').addEventListener('change', updateModelPreview);

// Provider modal
function openAddProviderModal() {
    document.getElementById('provider-form').reset();
    document.getElementById('provider-original-id').value = '';
    document.getElementById('provider-modal-title').textContent = 'Add Provider';
    openModal('provider-modal');
}

async function editProvider(id) {
    try {
        const config = await API.getConfig();
        const provider = config.providers[id];
        
        if (!provider) {
            showToast('Provider not found', 'error');
            return;
        }
        
        document.getElementById('provider-original-id').value = id;
        document.getElementById('provider-id').value = id;
        document.getElementById('provider-base-url').value = provider.baseUrl || '';
        document.getElementById('provider-api').value = provider.api || 'openai-completions';
        document.getElementById('provider-auth').value = provider.auth || 'api-key';
        document.getElementById('provider-api-key').value = provider.apiKey || '';
        document.getElementById('provider-auth-header').checked = provider.authHeader !== false;
        
        // Headers
        const headersContainer = document.getElementById('provider-headers');
        headersContainer.innerHTML = '';
        
        if (provider.headers && Object.keys(provider.headers).length > 0) {
            for (const [key, value] of Object.entries(provider.headers)) {
                headersContainer.innerHTML += `
                    <div class="key-value-row">
                        <input type="text" class="form-control header-key" placeholder="Header name" value="${key}">
                        <input type="text" class="form-control header-value" placeholder="Header value" value="${value}">
                        <button type="button" class="btn btn-icon remove-header">&minus;</button>
                    </div>
                `;
            }
        } else {
            headersContainer.innerHTML = `
                <div class="key-value-row">
                    <input type="text" class="form-control header-key" placeholder="Header name">
                    <input type="text" class="form-control header-value" placeholder="Header value">
                    <button type="button" class="btn btn-icon remove-header">&minus;</button>
                </div>
            `;
        }
        
        document.getElementById('provider-modal-title').textContent = 'Edit Provider';
        openModal('provider-modal');
    } catch (err) {
        showToast(`Failed to load provider: ${err.message}`, 'error');
    }
}

async function deleteProvider(id) {
    if (!confirm(`Are you sure you want to delete provider "${id}"?`)) {
        return;
    }
    
    try {
        await API.deleteProvider(id);
        showToast('Provider deleted', 'success');
        loadProviders();
    } catch (err) {
        showToast(`Failed to delete provider: ${err.message}`, 'error');
    }
}

document.getElementById('add-provider-btn').addEventListener('click', openAddProviderModal);

document.getElementById('add-header-btn').addEventListener('click', () => {
    const container = document.getElementById('provider-headers');
    const row = document.createElement('div');
    row.className = 'key-value-row';
    row.innerHTML = `
        <input type="text" class="form-control header-key" placeholder="Header name">
        <input type="text" class="form-control header-value" placeholder="Header value">
        <button type="button" class="btn btn-icon remove-header">&minus;</button>
    `;
    container.appendChild(row);
});

document.getElementById('provider-headers').addEventListener('click', (e) => {
    if (e.target.classList.contains('remove-header')) {
        const rows = document.querySelectorAll('#provider-headers .key-value-row');
        if (rows.length > 1) {
            e.target.closest('.key-value-row').remove();
        } else {
            // Clear the inputs instead of removing the last row
            const row = e.target.closest('.key-value-row');
            row.querySelectorAll('input').forEach(input => input.value = '');
        }
    }
});

document.getElementById('save-provider-btn').addEventListener('click', async () => {
    const originalId = document.getElementById('provider-original-id').value;
    const id = document.getElementById('provider-id').value.trim();
    
    if (!id) {
        showToast('Provider ID is required', 'error');
        return;
    }
    
    // Collect headers
    const headers = {};
    document.querySelectorAll('#provider-headers .key-value-row').forEach(row => {
        const key = row.querySelector('.header-key').value.trim();
        const value = row.querySelector('.header-value').value.trim();
        if (key) headers[key] = value;
    });
    
    const provider = {
        baseUrl: document.getElementById('provider-base-url').value.trim(),
        api: document.getElementById('provider-api').value,
        auth: document.getElementById('provider-auth').value,
        apiKey: document.getElementById('provider-api-key').value.trim(),
        authHeader: document.getElementById('provider-auth-header').checked,
        headers,
    };
    
    try {
        await API.saveProvider(id, provider);
        
        // If editing and ID changed, delete old one
        if (originalId && originalId !== id) {
            await API.deleteProvider(originalId);
        }
        
        closeModal('provider-modal');
        showToast('Provider saved', 'success');
        loadProviders();
    } catch (err) {
        showToast(`Failed to save provider: ${err.message}`, 'error');
    }
});

// Auth modal
function openAddAuthModal() {
    document.getElementById('auth-form').reset();
    document.getElementById('auth-original-id').value = '';
    document.getElementById('auth-modal-title').textContent = 'Add Auth Profile';
    document.getElementById('delete-auth-btn').classList.add('hidden');
    
    // Load providers into select
    API.getBuiltinProviders().then(providers => {
        const select = document.getElementById('auth-provider');
        select.innerHTML = '<option value="">Select provider...</option>' +
            Object.keys(providers).map(id => `<option value="${id}">${id}</option>`).join('');
    });
    
    openModal('auth-modal');
}

async function editAuthProfile(id) {
    try {
        const profiles = await API.getAuthProfiles();
        const profile = profiles.find(p => p.id === id);
        
        if (!profile) {
            showToast('Profile not found', 'error');
            return;
        }
        
        // Load providers
        const providers = await API.getBuiltinProviders();
        const select = document.getElementById('auth-provider');
        select.innerHTML = '<option value="">Select provider...</option>' +
            Object.keys(providers).map(pid => 
                `<option value="${pid}" ${pid === profile.provider ? 'selected' : ''}>${pid}</option>`
            ).join('');
        
        document.getElementById('auth-original-id').value = id;
        document.getElementById('auth-profile-id').value = id;
        document.getElementById('auth-type').value = profile.type;
        document.getElementById('auth-key').value = profile.key || '';
        document.getElementById('auth-token').value = profile.token || '';
        document.getElementById('auth-access').value = profile.access || '';
        document.getElementById('auth-refresh').value = profile.refresh || '';
        document.getElementById('auth-expires').value = profile.expires || '';
        document.getElementById('auth-email').value = profile.email || '';
        document.getElementById('auth-base-url').value = profile.baseUrl || '';
        
        document.getElementById('auth-modal-title').textContent = 'Edit Auth Profile';
        document.getElementById('delete-auth-btn').classList.remove('hidden');
        
        updateAuthTypeVisibility();
        openModal('auth-modal');
    } catch (err) {
        showToast(`Failed to load profile: ${err.message}`, 'error');
    }
}

async function deleteAuthProfile(id) {
    if (!confirm(`Are you sure you want to delete auth profile "${id}"?`)) {
        return;
    }
    
    try {
        await API.deleteAuthProfile(id);
        closeModal('auth-modal');
        showToast('Auth profile deleted', 'success');
        loadAuthProfiles();
    } catch (err) {
        showToast(`Failed to delete profile: ${err.message}`, 'error');
    }
}

function updateAuthTypeVisibility() {
    const type = document.getElementById('auth-type').value;
    
    document.getElementById('auth-key-group').classList.toggle('hidden', type !== 'api_key');
    document.getElementById('auth-token-group').classList.toggle('hidden', type !== 'token');
    document.getElementById('auth-oauth-group').classList.toggle('hidden', type !== 'oauth');
}

document.getElementById('add-auth-btn').addEventListener('click', openAddAuthModal);
document.getElementById('auth-type').addEventListener('change', updateAuthTypeVisibility);

document.getElementById('save-auth-btn').addEventListener('click', async () => {
    const originalId = document.getElementById('auth-original-id').value;
    const id = document.getElementById('auth-profile-id').value.trim();
    
    if (!id) {
        showToast('Profile ID is required', 'error');
        return;
    }
    
    const provider = document.getElementById('auth-provider').value;
    if (!provider) {
        showToast('Provider is required', 'error');
        return;
    }
    
    const type = document.getElementById('auth-type').value;
    const profile = {
        provider,
        type,
        email: document.getElementById('auth-email').value.trim() || null,
        expires: parseInt(document.getElementById('auth-expires').value) || null,
        baseUrl: document.getElementById('auth-base-url').value.trim() || null,
    };
    
    if (type === 'api_key') {
        profile.key = document.getElementById('auth-key').value.trim() || null;
    } else if (type === 'token') {
        profile.token = document.getElementById('auth-token').value.trim() || null;
    } else if (type === 'oauth') {
        profile.access = document.getElementById('auth-access').value.trim() || null;
        profile.refresh = document.getElementById('auth-refresh').value.trim() || null;
    }
    
    try {
        await API.saveAuthProfile(id, profile);
        
        if (originalId && originalId !== id) {
            await API.deleteAuthProfile(originalId);
        }
        
        closeModal('auth-modal');
        showToast('Auth profile saved', 'success');
        loadAuthProfiles();
    } catch (err) {
        showToast(`Failed to save profile: ${err.message}`, 'error');
    }
});

document.getElementById('delete-auth-btn').addEventListener('click', async () => {
    const id = document.getElementById('auth-original-id').value;
    if (id) {
        await deleteAuthProfile(id);
    }
});

// Settings
document.getElementById('save-settings-btn').addEventListener('click', async () => {
    const provider = document.getElementById('default-provider').value;
    const model = document.getElementById('default-model').value;
    
    if (!provider || !model) {
        showToast('Please select both a provider and a model', 'warning');
        return;
    }
    
    const defaultModel = `${provider}/${model}`;
    
    try {
        const config = await API.getConfig();
        config.defaultModel = defaultModel;
        await API.saveConfig(config);
        showToast('Settings saved', 'success');
    } catch (err) {
        showToast(`Failed to save settings: ${err.message}`, 'error');
    }
});

// Initialize
loadProviders();
