import test from 'node:test'
import assert from 'node:assert/strict'
import { sortAccounts } from './account-sort.js'

const accounts = [
  { id: 'old-bili', platform: 'bili', name: '张三' },
  { id: 'old-douyin', platform: 'douyin', name: '李四' },
  { id: 'channels', platform: 'channels', name: '账号10', connected_at: 20 },
  { id: 'xhs', platform: 'xhs', name: '账号2', connected_at: 10 },
  { id: 'new-douyin', platform: 'douyin', name: 'DemoCreator', connected_at: 30 },
]
const ids = rows => rows.map(row => row.id)

test('connection time keeps legacy order and orders new accounts by timestamp', () => {
  const asc = sortAccounts(accounts)
  assert.deepEqual(ids(asc), ['old-bili', 'old-douyin', 'xhs', 'channels', 'new-douyin'])
  assert.deepEqual(ids(sortAccounts(accounts, { key: 'connected_at', direction: 'desc' })), ids(asc).reverse())
  assert.deepEqual(ids(accounts), ['old-bili', 'old-douyin', 'channels', 'xhs', 'new-douyin'])
})

test('platform grouping uses the fixed platform order and connection time within groups', () => {
  const asc = sortAccounts(accounts, { key: 'platform' })
  assert.deepEqual(ids(asc), ['old-bili', 'old-douyin', 'new-douyin', 'xhs', 'channels'])
  assert.deepEqual(ids(sortAccounts(accounts, { key: 'platform', direction: 'desc' })), ids(asc).reverse())
})

test('names use pinyin, alphabetic and natural number ordering', () => {
  const asc = sortAccounts(accounts, { key: 'name' })
  const names = asc.map(row => row.name)
  assert.ok(names.indexOf('李四') < names.indexOf('张三'))
  assert.ok(names.indexOf('账号2') < names.indexOf('账号10'))
  assert.deepEqual(ids(sortAccounts(accounts, { key: 'name', direction: 'desc' })), ids(asc).reverse())
  const latin = sortAccounts([{ name: 'Zoe' }, { name: 'alice' }, { name: 'Bob' }], { key: 'name' })
  assert.deepEqual(latin.map(row => row.name), ['alice', 'Bob', 'Zoe'])
})
