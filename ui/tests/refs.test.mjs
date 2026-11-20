// A ref tested or compared WITHOUT `.value` in <script setup> code.
//
// In the template a ref unwraps itself; in script code it does not, and the ref object is always
// truthy and never equal to a string. `admin.password !== adminPassword2` therefore said "the two
// passwords are different" for every pair, and no fresh install of v1.7 could create its admin
// (#160). The build is happy with it and nothing at runtime throws, so it is checked here, over
// every component, with Vue's own compiler deciding which names are refs.
//
//   cd ui && node --test tests/refs.test.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const UI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const { parse, compileScript, walk } = createRequire(path.join(UI, 'package.json'))('vue/compiler-sfc')

// Comparisons whose answer a ref object makes meaningless.
const COMPARE = new Set(['===', '!==', '==', '!=', '<', '>', '<=', '>='])
const FUNCTION = new Set(['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression'])

/** Names a pattern binds: `x`, `{a, b: c}`, `[d, ...e]`, `f = 1`. */
function bound(p, out = []) {
  if (!p) return out
  if (p.type === 'Identifier') out.push(p.name)
  else if (p.type === 'ObjectPattern') p.properties.forEach((q) => bound(q.type === 'RestElement' ? q.argument : q.value, out))
  else if (p.type === 'ArrayPattern') p.elements.forEach((q) => bound(q, out))
  else if (p.type === 'AssignmentPattern') bound(p.left, out)
  else if (p.type === 'RestElement') bound(p.argument, out)
  return out
}

/** What a function declares for itself — its parameters and its own variables — which shadows a
 *  same-named ref inside it (`watch(loaded, (ready) => { if (!ready) … })` is not about the ref). */
function ownNames(fn) {
  const names = new Set(fn.params.flatMap((p) => bound(p)))
  walk(fn.body, {
    enter(node) {
      if (node !== fn.body && FUNCTION.has(node.type)) return this.skip()
      if (node.type === 'VariableDeclarator') bound(node.id).forEach((n) => names.add(n))
    },
  })
  return names
}

/** Every place in one SFC's <script setup> where a ref is tested or compared bare. */
export function bareRefUses(source, filename = 'x.vue') {
  const { descriptor } = parse(source, { filename })
  if (!descriptor.scriptSetup) return []
  const compiled = compileScript(descriptor, { id: 'refs-test' })
  const refs = new Set(Object.entries(compiled.bindings || {})
    .filter(([, kind]) => kind === 'setup-ref').map(([name]) => name))
  if (!refs.size) return []
  const offset = descriptor.scriptSetup.loc.start.line - 1
  const scopes = []
  const shadowed = (name) => scopes.some((s) => s.has(name))
  const found = []
  walk(compiled.scriptSetupAst ? { type: 'Program', body: compiled.scriptSetupAst } : compiled.scriptAst, {
    enter(node, parent, key) {
      if (FUNCTION.has(node.type)) { scopes.push(ownNames(node)); return }
      if (node.type !== 'Identifier' || !refs.has(node.name) || !parent || shadowed(node.name)) return
      const bare = (parent.type === 'BinaryExpression' && COMPARE.has(parent.operator))
        || (parent.type === 'UnaryExpression' && parent.operator === '!')
        || (parent.type === 'LogicalExpression' && key === 'left')
        || (key === 'test' && ['IfStatement', 'ConditionalExpression', 'WhileStatement',
                               'DoWhileStatement', 'ForStatement'].includes(parent.type))
      if (bare) found.push({ name: node.name, line: node.loc.start.line + offset })
    },
    leave(node) { if (FUNCTION.has(node.type)) scopes.pop() },
  })
  return found
}

function components(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) components(p, out)
    else if (p.endsWith('.vue')) out.push(p)
  }
  return out
}

test('no component tests or compares a ref without .value', () => {
  const problems = []
  for (const file of components(path.join(UI, 'src'))) {
    for (const { name, line } of bareRefUses(fs.readFileSync(file, 'utf8'), file)) {
      problems.push(`${path.relative(UI, file)}:${line}  \`${name}\` is a ref — use \`${name}.value\``)
    }
  }
  assert.deepEqual(problems, [])
})

// A guard on the guard: the exact line that shipped in v1.7 must be caught, and the two shapes it
// must NOT flag — the fixed line, and a callback parameter that happens to share a ref's name.
const sfc = (body) => `<script setup>\nimport { ref } from 'vue'\n${body}\n</script>\n`

test('it catches the v1.7 wizard line', () => {
  const found = bareRefUses(sfc(
    "const admin = { password: '' }\nconst adminPassword2 = ref('')\n" +
    'function next() { if (admin.password !== adminPassword2) return }'))
  assert.deepEqual(found.map((f) => f.name), ['adminPassword2'])
})

test('it catches a ref used as a condition', () => {
  const found = bareRefUses(sfc('const busy = ref(false)\nfunction go() { if (busy) return; return !busy }'))
  assert.equal(found.length, 2)
})

test('it accepts .value, and a parameter that shadows a ref', () => {
  assert.deepEqual(bareRefUses(sfc(
    "const a = ref('')\nconst b = ref('')\nconst ready = ref(false)\n" +
    'function next() { return a.value !== b.value }\n' +
    'const f = (ready) => { if (!ready) return }')), [])
})
