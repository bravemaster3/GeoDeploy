// What an API error says, as one sentence.
//
// FastAPI's own validation errors (422) carry `detail` as a LIST of objects —
// `[{loc: ['body', 'password'], msg: 'String should have at least 8 characters', …}]` — while every
// error the routers raise themselves carries a string. Seventy-odd screens show
// `err.response.data.detail` as it comes, so a 422 rendered as raw JSON in a red box. The API
// client's interceptor runs this once, on the way in, so all of them read a sentence.

// Where the field sits in the request is noise to the person reading the message.
const LOCATIONS = new Set(['body', 'query', 'path', 'header', 'cookie'])

export function detailText(detail) {
  if (!Array.isArray(detail)) return detail
  const parts = detail.map((d) => {
    if (d == null || typeof d !== 'object') return String(d)
    const field = (Array.isArray(d.loc) ? d.loc : []).filter((p) => !LOCATIONS.has(p)).join('.')
    const msg = d.msg || 'is not valid'
    return field ? `${field}: ${msg}` : msg
  })
  return parts.join('; ') || 'The request was not valid.'
}
