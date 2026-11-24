// Meshkah: turns a recorded poem into text with the time of every letter (ElevenLabs Scribe),
// so «ميزان الشعر» can scan it the way it was chanted. The ElevenLabs key stays on the server.
import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://gikbfmyiciabrkuikftp.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_PUBLISHABLE_KEY || 'sb_publishable_y3MXoYQ8_ZYySh7MOtRLBw_SMphq4W5';
const DAILY_LIMIT = Number(process.env.MIDAD_DAILY_LIMIT || 60);
const ADMIN_DAILY_LIMIT = Number(process.env.MIDAD_ADMIN_DAILY_LIMIT || 400);
const STT_MODEL = process.env.ELEVENLABS_STT_MODEL || 'scribe_v2';
const MAX_AUDIO_BYTES = 8 * 1024 * 1024; // about a minute of audio, with room to spare
const AUDIO_TYPES = /^audio\/|^video\/(webm|mp4)/;

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ code: 'method_not_allowed' });
  if (!process.env.ELEVENLABS_API_KEY) return res.status(503).json({ code: 'voice_not_configured' });

  const token = String(req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  if (!token) return res.status(401).json({ code: 'not_granted' });
  const sb = createClient(SUPABASE_URL, SUPABASE_KEY, {
    global: { headers: { Authorization: `Bearer ${token}` } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: auth, error: authError } = await sb.auth.getUser(token);
  if (authError || !auth?.user) return res.status(401).json({ code: 'not_granted' });

  const since = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const [{ count }, { data: admin }] = await Promise.all([
    sb.from('midad_ai_usage').select('id', { count: 'exact', head: true }).gte('created_at', since),
    sb.from('midad_admins').select('user_id').eq('user_id', auth.user.id).maybeSingle(),
  ]);
  if ((count || 0) >= (admin ? ADMIN_DAILY_LIMIT : DAILY_LIMIT)) return res.status(429).json({ code: 'rate_limited' });

  const body = typeof req.body === 'string' ? safeJson(req.body) : req.body || {};
  const type = String(body.media_type || '').split(';')[0];
  if (typeof body.data !== 'string' || !AUDIO_TYPES.test(type)) return res.status(400).json({ code: 'invalid_request' });
  const audio = Buffer.from(body.data, 'base64');
  if (audio.length < 1000) return res.status(400).json({ code: 'audio_too_short' });
  if (audio.length > MAX_AUDIO_BYTES) return res.status(413).json({ code: 'audio_too_large' });

  await sb.from('midad_ai_usage').insert({ user_id: auth.user.id });

  const form = new FormData();
  form.append('model_id', STT_MODEL);
  form.append('language_code', 'ar');
  form.append('timestamps_granularity', 'character');
  form.append('tag_audio_events', 'false');
  form.append('file', new Blob([audio], { type }), 'poem.' + (type.split('/')[1] || 'webm'));

  try {
    const r = await fetch('https://api.elevenlabs.io/v1/speech-to-text', {
      method: 'POST',
      headers: { 'xi-api-key': process.env.ELEVENLABS_API_KEY },
      body: form,
    });
    if (!r.ok) {
      const detail = await r.text().catch(() => '');
      console.error('elevenlabs stt failed:', r.status, detail.slice(0, 300));
      const code = r.status === 401 ? 'voice_not_configured' : r.status === 429 ? 'voice_busy' : r.status === 402 || /quota|credit/i.test(detail) ? 'voice_quota' : 'upstream_error';
      return res.status(502).json({ code });
    }
    const j = await r.json();
    // Keep only what the page needs: each word, its time, and the time of each of its letters.
    const words = (j.words || []).filter(w => w.type === 'word').map(w => ({
      t: w.text, s: round(w.start), e: round(w.end),
      c: Array.isArray(w.characters) ? w.characters.map(c => ({ t: c.text, s: round(c.start), e: round(c.end) })) : undefined,
    }));
    return res.status(200).json({ text: j.text || '', words });
  } catch (error) {
    console.error('elevenlabs stt error:', error?.message);
    return res.status(502).json({ code: 'upstream_error' });
  }
}

const round = n => (typeof n === 'number' ? Math.round(n * 1000) / 1000 : null);
function safeJson(s) {
  try { return JSON.parse(s); } catch { return {}; }
}
