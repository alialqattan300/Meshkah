// Meshkah: summaries, quizzes and search expansion through Claude.
// The browser sends the signed-in user's Supabase token; the API key stays on the server.
import Anthropic from '@anthropic-ai/sdk';
import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://gikbfmyiciabrkuikftp.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_PUBLISHABLE_KEY || 'sb_publishable_y3MXoYQ8_ZYySh7MOtRLBw_SMphq4W5';
const DAILY_LIMIT = Number(process.env.MIDAD_DAILY_LIMIT || 60);
const MODEL = process.env.MIDAD_MODEL || 'claude-opus-5-5';
const MAX_PROMPT_BYTES = 400000;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ code: 'method_not_allowed' });
  if (!process.env.ANTHROPIC_API_KEY) return res.status(503).json({ code: 'not_configured' });

  const token = String(req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  if (!token) return res.status(401).json({ code: 'not_granted' });
  const sb = createClient(SUPABASE_URL, SUPABASE_KEY, {
    global: { headers: { Authorization: `Bearer ${token}` } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: auth, error: authError } = await sb.auth.getUser(token);
  if (authError || !auth?.user) return res.status(401).json({ code: 'not_granted' });

  const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const { count } = await sb.from('midad_ai_usage').select('id', { count: 'exact', head: true }).gte('created_at', since);
  if ((count || 0) >= DAILY_LIMIT) return res.status(429).json({ code: 'rate_limited' });

  const body = typeof req.body === 'string' ? safeJson(req.body) : req.body || {};
  const prompt = body.prompt;
  if (typeof prompt !== 'string' || !prompt.trim()) return res.status(400).json({ code: 'invalid_request' });
  if (Buffer.byteLength(prompt) > MAX_PROMPT_BYTES) return res.status(413).json({ code: 'prompt_too_large' });

  const content = [];
  for (const im of (Array.isArray(body.images) ? body.images : []).slice(0, 4)) {
    if (im && IMAGE_TYPES.has(im.media_type) && typeof im.data === 'string' && im.data.length < 4_000_000) {
      content.push({ type: 'image', source: { type: 'base64', media_type: im.media_type, data: im.data } });
    }
  }
  content.push({ type: 'text', text: prompt });

  await sb.from('midad_ai_usage').insert({ user_id: auth.user.id });

  const client = new Anthropic();
  try {
    const stream = client.beta.messages.stream({
      model: MODEL,
      max_tokens: 16000,
      betas: ['server-side-fallback-2026-07-01'],
      fallbacks: 'default',
      output_config: { effort: 'medium' },
      messages: [{ role: 'user', content }],
    });
    stream.on('text', (delta) => {
      if (!res.headersSent) {
        res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        res.setHeader('Cache-Control', 'no-store');
      }
      res.write(delta);
    });
    const final = await stream.finalMessage();
    if (!res.headersSent) res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    if (final.stop_reason === 'refusal') res.write('\n[[ERROR:refusal]]');
    res.end();
  } catch (error) {
    let code = 'upstream_error';
    if (error instanceof Anthropic.RateLimitError) code = 'rate_limited';
    else if (error instanceof Anthropic.AuthenticationError) code = 'not_configured';
    else if (error instanceof Anthropic.BadRequestError && /too long|too large|prompt is too long/i.test(error.message)) code = 'prompt_too_large';
    console.error('claude request failed:', error?.status, error?.message);
    if (!res.headersSent) return res.status(code === 'rate_limited' ? 429 : 502).json({ code });
    res.write(`\n[[ERROR:${code}]]`);
    res.end();
  }
}

function safeJson(s) {
  try { return JSON.parse(s); } catch { return {}; }
}
