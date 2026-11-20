// lib/apiError.detailText — a FastAPI validation error, read as a sentence.
//   cd ui && node --test tests/apiError.test.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { detailText } from '../src/lib/apiError.js'

test('a 422 list becomes one sentence naming the field', () => {
  // The body FastAPI sends for the wizard's short password (pydantic v2), verbatim in shape.
  const detail = [{ type: 'string_too_short', loc: ['body', 'password'],
                    msg: 'String should have at least 8 characters', input: 'test', ctx: { min_length: 8 } }]
  assert.equal(detailText(detail), 'password: String should have at least 8 characters')
})

test('several problems are all named, nested fields keep their path', () => {
  const detail = [
    { loc: ['body', 'email'], msg: 'value is not a valid email address' },
    { loc: ['body', 'style', 'color'], msg: 'Field required' },
  ]
  assert.equal(detailText(detail),
    'email: value is not a valid email address; style.color: Field required')
})

test('a string detail — what every router raises — passes through untouched', () => {
  assert.equal(detailText('Email already registered.'), 'Email already registered.')
  assert.equal(detailText(undefined), undefined)
})

test('odd shapes still give text, never [object Object]', () => {
  assert.equal(detailText([{ msg: 'Bad input' }]), 'Bad input')
  assert.equal(detailText(['plain']), 'plain')
  assert.equal(detailText([]), 'The request was not valid.')
})
