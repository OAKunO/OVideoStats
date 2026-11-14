const platformOrder = { bili: 0, douyin: 1, xhs: 2, channels: 3 }
const nameOrder = new Intl.Collator('zh-CN-u-co-pinyin', { numeric: true, sensitivity: 'base' })

export function sortAccounts(accounts, { key = 'connected_at', direction = 'asc' } = {}) {
  const timeOrder = (a, b) => (a.account.connected_at || 0) - (b.account.connected_at || 0) || a.index - b.index
  return accounts.map((account, index) => ({ account, index })).sort((a, b) => {
    let order
    if (key === 'platform') {
      order = (platformOrder[a.account.platform] ?? 4) - (platformOrder[b.account.platform] ?? 4) || timeOrder(a, b)
    } else if (key === 'name') {
      order = nameOrder.compare(a.account.name || '', b.account.name || '') || timeOrder(a, b)
    } else {
      // Older connections have no timestamp; their saved order predates new ones.
      order = timeOrder(a, b)
    }
    return direction === 'desc' ? -order : order
  }).map(({ account }) => account)
}
