/* The rules for model services that the settings page applies to what a person enters: which model names
   and API protocols are allowed, which providers have a known official endpoint, how an API connection is
   checked, how a model is named and identified, and which capability a model name suggests. They are plain
   functions of their arguments, used by settings.js (the forms and the developer editor). Loaded before
   settings.js. Publishes Memorive.settingsModelRules. */
(() => {
  'use strict';
  const modelSegmentPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;

  const apiModelPattern = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

  const cliModelPattern = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

  const apiProtocolValues = new Set(['AUTO', 'OPENAI_COMPATIBLE', 'ANTHROPIC', 'GEMINI']);

  const apiProtocolLabels = Object.freeze({
    AUTO: '官方接口（自动）',
    OPENAI_COMPATIBLE: 'OpenAI 兼容 / 中转站',
    ANTHROPIC: 'Anthropic 兼容',
    GEMINI: 'Gemini 兼容'
  });

  const KIMI_API_ENDPOINTS = Object.freeze({
    CN: 'https://api.moonshot.cn/v1',
    AI: 'https://api.moonshot.ai/v1'
  });

  const modelDescriptor = (service) => {
    const parts = [service?.model_name, service?.tier, service?.thinking_mode]
      .map((value) => typeof value === 'string' ? value.trim() : '')
      .filter(Boolean);
    return parts.length >= 2 ? parts.join('-') : (service?.provider || '未命名模型服务');
  };

  const providerIdentity = (value) => String(value || '')
    .normalize('NFKC')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase();

  const isKimiService = (service = {}) => {
    const labels = [service.provider, service.credential_ref]
      .map((value) => providerIdentity(value).replace(/[^\p{L}\p{N}]/gu, ''));
    return labels.some((label) => label.includes('kimi')
      || label.includes('moonshot') || label.includes('月之暗面'));
  };

  const applyKimiAutoDetection = (service) => {
    if (!isKimiService(service)) return;
    const endpoint = String(service.api_base_url || '').trim().replace(/\/+$/, '');
    // Keep custom compatible endpoints; official Kimi endpoints use the existing resolver.
    if (endpoint && !Object.values(KIMI_API_ENDPOINTS).includes(endpoint)) return;
    service.api_platform = 'AUTO';
  };

  const isKnownOfficialApiProvider = (provider) => {
    const identity = providerIdentity(provider).replace(/[^a-z0-9]/g, '');
    return [
      /^deepseek(?:apikey[a-z0-9]*)?$/,
      /^(?:anthropic|claude)(?:apikey[a-z0-9]*)?$/,
      /^openai(?:apikey[a-z0-9]*)?$/,
      /^(?:gemini|googleai)(?:apikey[a-z0-9]*)?$/,
      /^siliconflow(?:apikey[a-z0-9]*)?$/
    ].some((pattern) => pattern.test(identity)) || isKimiService({ provider });
  };

  const inferApiModelCapability = (provider, modelName) => {
    const model = String(modelName || '').normalize('NFKC').trim().toLowerCase().replace(/^\/+|\/+$/g, '');
    // Keep the known native families aligned with model_capabilities.py.
    if (model === 'qwen/qwen3-vl-embedding-8b') return 'EMBEDDING';
    if (model === 'bge-m3' || model.startsWith('bge-m3:')
        || model.endsWith('/bge-m3') || model.includes('/bge-m3:')) return 'EMBEDDING';
    if (model.includes('reranker')) return 'RERANKER';
    if (model.endsWith('deepseek-ai/deepseek-ocr') || model.endsWith('deepseek-ocr')) return 'VISION_OCR';
    return 'CHAT';
  };

  const apiConnectionState = (provider, protocolValue, baseUrlValue) => {
    const api_protocol = String(protocolValue || 'AUTO').trim().toUpperCase();
    const api_base_url = String(baseUrlValue || '').trim().replace(/\/+$/, '');
    if (!apiProtocolValues.has(api_protocol)) {
      return { valid: false, code: 'API_PROTOCOL_INVALID', api_protocol, api_base_url };
    }
    if (api_protocol === 'AUTO') {
      if (api_base_url) return { valid: false, code: 'API_CONNECTION_INCOMPLETE', api_protocol, api_base_url };
      if (!isKnownOfficialApiProvider(provider)) {
        return { valid: false, code: 'API_PROVIDER_PROTOCOL_REQUIRED', api_protocol, api_base_url };
      }
      return { valid: true, api_protocol, api_base_url };
    }
    if (!api_base_url) return { valid: false, code: 'API_BASE_URL_REQUIRED', api_protocol, api_base_url };
    try {
      const parsed = new URL(api_base_url);
      const hostname = parsed.hostname.replace(/^\[|\]$/g, '').toLowerCase();
      if (parsed.protocol !== 'https:' || parsed.username || parsed.password
          || parsed.search || parsed.hash || !hostname
          || hostname === 'localhost' || hostname.endsWith('.localhost')) {
        throw new Error('API_BASE_URL_INVALID');
      }
    } catch (_) {
      return { valid: false, code: 'API_BASE_URL_INVALID', api_protocol, api_base_url };
    }
    return { valid: true, api_protocol, api_base_url };
  };

  const modelIdentity = (service) => [service?.model_name, service?.tier, service?.thinking_mode]
    .map((value) => typeof value === 'string' ? value.trim().normalize('NFKC').toLowerCase() : '')
    .join('|');

  const examProviderFamily = (service = {}) => {
    const protocol = String(service.api_protocol || 'AUTO').trim().toUpperCase();
    if (protocol !== 'AUTO') return protocol;
    const provider = String(service.provider || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
    if (/^(anthropic|claude)/.test(provider)) return 'ANTHROPIC';
    if (/^openai/.test(provider)) return 'OPENAI';
    if (/^(gemini|googleai)/.test(provider)) return 'GEMINI';
    if (/^(deepseek|siliconflow|硅基流动|硅基)/.test(provider)) return 'OPENAI_COMPATIBLE';
    return '';
  };

  const localModelDisplayName = (model = {}) => {
    const raw = String(model.display_name || model.model_name || model.profile_ref || '').trim();
    return raw.startsWith('[本地]') ? raw : `[本地] ${raw || '本地模型'}`;
  };

  const normalizeLocalCapabilities = (model = {}) => {
    const raw = Array.isArray(model.capabilities)
      ? model.capabilities
      : [model.capability || inferApiModelCapability(model.endpoint_name, model.model_name)];
    const normalized = raw
      .map((value) => String(value || '').trim().toUpperCase())
      .filter(Boolean);
    return normalized.length ? [...new Set(normalized)] : ['CHAT'];
  };

  Memorive.settingsModelRules = Object.freeze({
    modelSegmentPattern,
    apiModelPattern,
    cliModelPattern,
    apiProtocolValues,
    apiProtocolLabels,
    KIMI_API_ENDPOINTS,
    modelDescriptor,
    providerIdentity,
    isKimiService,
    applyKimiAutoDetection,
    isKnownOfficialApiProvider,
    inferApiModelCapability,
    apiConnectionState,
    modelIdentity,
    examProviderFamily,
    localModelDisplayName,
    normalizeLocalCapabilities,
  });
})();
