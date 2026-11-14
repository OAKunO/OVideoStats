import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeAppearance, resolveTheme } from './appearance.js'

test('explicit appearance stays selected when system appearance changes', () => {
  for (const system of ['light', 'dark']) {
    assert.equal(resolveTheme('light', system), 'light')
    assert.equal(resolveTheme('dark', system), 'dark')
  }
})

test('system mode follows the current system while preserving the saved preference', () => {
  const saved = normalizeAppearance('system')
  assert.equal(saved, 'system')
  assert.equal(resolveTheme(saved, 'dark'), 'dark')
  assert.equal(resolveTheme(saved, 'light'), 'light')
  assert.equal(normalizeAppearance('invalid'), 'light')
})
