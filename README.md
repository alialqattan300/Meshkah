# Meshkah (مشكاة)

موقع قراءة مشترك: مكتبة كتب PDF و EPUB، بحث مرن (كلمات مشابهة، أسماء الرواة)، تظليل وعلامات، يحفظ مكان القراءة، وتلخيص واختبارات من Claude.

- `index.html`: الموقع كله، بدون build.
- `api/claude.js`: دالة Vercel تمرر طلبات التلخيص والاختبارات لـ Claude. تتحقق من تسجيل الدخول في Supabase، وتحد كل مستخدم بـ `MIDAD_DAILY_LIMIT` طلب في اليوم (الافتراضي 60).
- `supabase-setup.sql`: يسوي الجداول (`midad_*`) ومخزن الكتب (`midad-books`) في مشروع Supabase جديد. الصقه في SQL Editor واضغط Run.
- فقط الحسابات في `midad_admins` تضيف وتحذف كتب.

## إعدادات Vercel (Environment Variables)

| المتغير | مطلوب | الوصف |
|---|---|---|
| `ANTHROPIC_API_KEY` | نعم | مفتاح Claude API من console.anthropic.com. لا تحطه في الكود. |
| `SUPABASE_URL` | لا | رابط مشروع Supabase. |
| `SUPABASE_PUBLISHABLE_KEY` | لا | المفتاح العام لمشروع Supabase. |
| `MIDAD_DAILY_LIMIT` | لا | عدد طلبات Claude لكل مستخدم في اليوم. |
| `MIDAD_MODEL` | لا | الموديل. الافتراضي `claude-opus-5-5`. |

رابط Supabase والمفتاح العام مكتوبين كمان في `index.html` (ثابتين `SB_URL` و `SB_KEY`).
