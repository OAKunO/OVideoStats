import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import './themes.css'
import { sortAccounts } from './account-sort.js'
import { appearanceOptions, normalizeAppearance, resolveTheme, themeBackground } from './appearance.js'
import appIcon from '../../packaging/app-icon.png'
const TOKEN = new URLSearchParams(location.hash.slice(1)).get('token') || ''
const platformMeta = {
  bili: { icon: '/icons/bili.ico', subtitle: 'BILIBILI' },
  douyin: { icon: '/icons/douyin.ico', subtitle: 'DOUYIN' },
  xhs: { icon: '/icons/xhs.ico', subtitle: 'XIAOHONGSHU' },
  channels: { icon: '/icons/channels.png', subtitle: 'CHANNELS' },
}
function PlatformIcon({ platform, size = 'normal' }) {
  return <span className={`brand-icon brand-icon-${size}`}><img src={platformMeta[platform].icon} alt=""/></span>
}
function AccountAvatar({ account }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => { setFailed(false) }, [account.avatar_url])
  if (!account.avatar_url || failed) return <PlatformIcon platform={account.platform}/>
  return <span className="account-avatar"><img className="account-avatar-image" src={`${account.avatar_url}&token=${encodeURIComponent(TOKEN)}`} alt="" onError={() => setFailed(true)}/><span className="account-avatar-platform"><PlatformIcon platform={account.platform} size="badge"/></span></span>
}
async function api(path, method = 'GET', data) {
  const response = await fetch(`/api${path}`, { method, headers: { 'X-Studio-Token': TOKEN, 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data) })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '请求失败')
  return result
}
function Icon({ name, size = 18 }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14"/>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>,
    down: <path d="M12 3v12m-5-5 5 5 5-5M4 17v3h16v-3"/>,
    folder: <path d="M3 6h6l2 2h10v11H3z"/>,
    check: <path d="m4 12 5 5L20 6"/>,
    close: <path d="M5 5 19 19M19 5 5 19"/>,
    sliders: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
    refresh: <><path d="M20 11a8 8 0 0 0-14-5L4 8"/><path d="M4 4v4h4M4 13a8 8 0 0 0 14 5l2-2M20 20v-4h-4"/></>,
    shield: <><path d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5z"/><path d="m9 12 2 2 4-4"/></>,
    chevron: <path d="m9 6 6 6-6 6"/>,
    ascending: <path d="M12 20V4m-5 5 5-5 5 5"/>,
    descending: <path d="M12 4v16m-5-5 5 5 5-5"/>,
    sun: <><circle cx="12" cy="12" r="3.5"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5"/></>,
    moon: <path d="M20.5 14.1A8.6 8.6 0 0 1 9.9 3.5a8.6 8.6 0 1 0 10.6 10.6Z"/>,
    system: <><circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17Z" fill="currentColor" stroke="none"/></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}
function SelectionBox({ checked, mixed = false, disabled, label, onChange }) {
  const input = useRef(null)
  useEffect(() => { input.current.indeterminate = mixed }, [mixed])
  return <input ref={input} className="selection-box" type="checkbox" checked={checked} disabled={disabled} aria-label={label} onChange={e => onChange(e.target.checked)}/>
}
const loginStatusLabels = { connected: '验证通过', expired: '登录过期', mismatch: '账号不一致', error: '检测失败', unchecked: '待验证' }
function checkTime(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false }) : ''
}
function App() {
  const [appearance, setAppearance] = useState(() => normalizeAppearance(document.documentElement.dataset.appearance))
  const [systemTheme, setSystemTheme] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  const theme = resolveTheme(appearance, systemTheme)
  const appearanceTarget = useRef(appearance)
  const appearanceChoice = useRef(0)
  const themeSave = useRef(Promise.resolve())
  const transitionTimer = useRef(null)
  const themeChanged = useRef(false)
  useEffect(() => () => {
    window.clearTimeout(transitionTimer.current)
    delete document.documentElement.dataset.themeTransition
  }, [])
  useLayoutEffect(() => {
    const root = document.documentElement
    if (root.dataset.theme !== theme && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      window.clearTimeout(transitionTimer.current)
      root.dataset.themeTransition = ''
      void root.offsetWidth
      transitionTimer.current = window.setTimeout(() => delete root.dataset.themeTransition, 450)
    }
    appearanceTarget.current = appearance
    root.dataset.appearance = appearance
    root.dataset.theme = theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', themeBackground(theme))
    if (appearance === 'system') window.pywebview?.api?.sync_theme?.('system')?.catch(() => {})
  }, [appearance, theme])
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    function systemChanged() {
      if (appearanceTarget.current === 'system') setSystemTheme(media.matches ? 'dark' : 'light')
    }
    function desktopReady() {
      window.pywebview?.api?.system_theme?.()?.then(value => {
        if (value === 'light' || value === 'dark') setSystemTheme(value)
      }).catch(() => {})
    }
    media.addEventListener('change', systemChanged)
    window.addEventListener('pywebviewready', desktopReady)
    desktopReady()
    return () => {
      media.removeEventListener('change', systemChanged)
      window.removeEventListener('pywebviewready', desktopReady)
    }
  }, [])
  const [state, setState] = useState({ accounts: [], pending_sessions: [], fields: {}, platforms: {}, export_folder: '', export_folder_available: false, account_sort: { key: 'connected_at', direction: 'asc' } })
  const [excludedIds, setExcludedIds] = useState(() => new Set())
  const selectedIds = state.accounts.filter(a => !excludedIds.has(a.id)).map(a => a.id)
  const [savingSort, setSavingSort] = useState(false)
  const [selected, setSelected] = useState(null)
  const [adding, setAdding] = useState(false)
  const [chosenPlatform, setChosenPlatform] = useState('bili')
  const [job, setJob] = useState(null)
  const [results, setResults] = useState(null)
  const [loginResults, setLoginResults] = useState(null)
  const [showLoginResults, setShowLoginResults] = useState(false)
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState(null)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [tab, setTab] = useState('accounts')
  const counts = useMemo(() => Object.keys(platformMeta).map(key => ({ key, count: state.accounts.filter(a => a.platform === key).length })), [state.accounts])
  const sortedAccounts = useMemo(() => sortAccounts(state.accounts, state.account_sort), [state.accounts, state.account_sort])
  function selectAccounts(ids, checked) {
    setExcludedIds(previous => {
      const next = new Set(previous)
      ids.forEach(id => checked ? next.delete(id) : next.add(id))
      return next
    })
  }
  async function changeSort(changes) {
    const previous = state.account_sort
    const next = { ...previous, ...changes }
    setSavingSort(true)
    setState(current => ({ ...current, account_sort: next }))
    try {
      await api('/account-sort', 'POST', next)
    } catch (e) {
      setState(current => ({ ...current, account_sort: previous }))
      setNotice(`排序保存失败：${e.message}`)
    } finally { setSavingSort(false) }
  }
  async function refresh() { setState(await api('/state')) }
  useEffect(() => {
    api('/state').then(next => {
      setState(next)
      if (!themeChanged.current) setAppearance(normalizeAppearance(next.theme))
    }).catch(e => setNotice(e.message))
  }, [])
  async function chooseAppearance(next) {
    const choice = ++appearanceChoice.current
    if (next === appearanceTarget.current) return
    themeChanged.current = true
    if (next === 'system') {
      const observed = window.pywebview?.api?.system_theme
        ? await window.pywebview.api.system_theme().catch(() => systemTheme)
        : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      if (choice !== appearanceChoice.current) return
      setSystemTheme(observed === 'light' || observed === 'dark' ? observed : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))
    }
    appearanceTarget.current = next
    setAppearance(next)
    themeSave.current = themeSave.current
      .then(() => api('/theme', 'POST', { theme: next }))
      .catch(e => setNotice(`主题保存失败：${e.message}`))
  }
  useEffect(() => {
    if (!job || job.state !== 'running') return
    const timer = setInterval(async () => {
      try {
        const next = await api(`/jobs/${job.id}`)
        setJob(next)
        if (next.state !== 'running') {
          clearInterval(timer)
          await refresh()
          if (next.kind === 'login' && next.state === 'done') setAdding(false)
          if (next.kind === 'reconnect' && next.state === 'done') { setSelected(null); setNotice('账号已重新连接') }
          if (next.kind === 'check-logins' && next.state === 'done') { setLoginResults(next.result); setShowLoginResults(true) }
          if (next.kind === 'export' && next.state === 'done') { setResults(next.result); setTab('activity') }
          if (next.state === 'error') setNotice(next.message)
        }
      } catch (e) { clearInterval(timer); setNotice(e.message) }
    }, 1200)
    return () => clearInterval(timer)
  }, [job?.id, job?.state])
  async function start(path, body) {
    try {
      setNotice('')
      const { job: id } = await api(path, 'POST', body)
      const kind = path === '/export' ? 'export' : path === '/accounts' ? 'login' : path.endsWith('/reconnect') ? 'reconnect' : 'check-logins'
      setJob({ id, kind, state: 'running', message: '正在准备…', completed: 0, total: kind === 'export' ? body.account_ids.length : 0 })
      if (kind === 'export') setResults(null)
      if (kind === 'check-logins') { setShowLoginResults(false); setLoginResults(null) }
    } catch (e) { setNotice(e.message) }
  }
  function openSettings(account) {
    setSelected(account.id)
    setDraft({ fields: [...account.fields], include_private: account.include_private })
  }
  async function saveSettings() {
    try {
      const updated = await api(`/accounts/${selected}/settings`, 'POST', draft)
      setState(current => ({ ...current, accounts: current.accounts.map(a => a.id === selected ? updated.account : a) }))
      setSelected(null)
      setNotice('导出设置已保存')
    } catch (e) { setNotice(e.message) }
  }
  async function deleteConnection() {
    try {
      await api(`/accounts/${selected}`, 'DELETE')
      setDeleteConfirm(false)
      setSelected(null)
      await refresh()
      setNotice('连接已删除')
    } catch (e) { setNotice(e.message) }
  }
  async function chooseFolder() {
    try {
      const selected = window.pywebview?.api?.choose_export_folder
        ? await window.pywebview.api.choose_export_folder()
        : (await api('/folder', 'POST', {})).folder
      if (selected) {
        const saved = await api('/export-folder', 'POST', { folder: selected })
        setState(current => ({ ...current, export_folder: saved.folder, export_folder_available: true }))
      }
    } catch (e) { setNotice(`文件夹选择失败：${e.message}`) }
  }
  const folder = state.export_folder || ''
  const folderReady = !!(folder && state.export_folder_available)
  const activeAccount = state.accounts.find(a => a.id === selected)
  const available = activeAccount?.available_fields
  const busy = job?.state === 'running'
  const progressTotal = job?.total || selectedIds.length
  const progressCompleted = Math.min(job?.completed || 0, progressTotal)
  const progressPercent = progressTotal ? Math.round(progressCompleted / progressTotal * 100) : 0
  return <div className="studio">
    <aside className="sidebar">
      <div className="side-brand"><img className="app-brand-icon" src={appIcon} alt="OVideoStats" width={34} height={34}/><span><strong>OVideoStats</strong><small>VIDEO DATA WORKSPACE</small></span></div>
      <div className="side-label">WORKSPACE</div>
      <button className={`nav-item ${tab === 'accounts' ? 'active' : ''}`} onClick={() => setTab('accounts')}><span className="nav-glyph">▦</span>账号管理 <span className="nav-count">{state.accounts.length.toString().padStart(2, '0')}</span></button>
      <button className={`nav-item ${tab === 'activity' ? 'active' : ''}`} onClick={() => setTab('activity')}><span className="nav-glyph">↗</span>导出记录 <Icon name="chevron" size={15}/></button>
      <div className="side-label platforms-label">PLATFORMS</div>
      {counts.map(({ key, count }) => {
        const ids = state.accounts.filter(a => a.platform === key).map(a => a.id)
        const selectedCount = ids.filter(id => !excludedIds.has(id)).length
        const name = state.platforms[key]?.name || key
        return <div key={key} className="platform-nav"><PlatformIcon platform={key} size="small"/><span>{name}</span><div className="platform-selection"><SelectionBox checked={count > 0 && selectedCount === count} mixed={selectedCount > 0 && selectedCount < count} disabled={busy || !count} label={`${name}全部账号`} onChange={checked => selectAccounts(ids, checked)}/><small title={`${name}共 ${count} 个账号，已选 ${selectedCount} 个`}>{String(count).padStart(2, '0')}</small></div></div>
      })}
    </aside>
    <main className="main-area">
      <header className="topbar"><div className="breadcrumbs">WORKSPACE <span>/</span> {tab === 'accounts' ? 'ACCOUNTS' : 'EXPORTS'}</div><div className="appearance-picker" role="group" aria-label="外观">{appearanceOptions.map(option => <button key={option.value} className="appearance-option" type="button" aria-label={option.label} aria-pressed={appearance === option.value} title={option.label} onClick={() => chooseAppearance(option.value)}><Icon name={option.icon} size={14}/></button>)}</div></header>
      <div className="content">
        {tab === 'accounts' ? <>
          <div className="section-head"><div><div className="section-index">ACCOUNTS / 账号管理</div><h1>账号工作台</h1><p>{state.accounts.length} 个账号 · 已选 {selectedIds.length} 个 · {counts.filter(p => p.count).length} 个平台 · {state.accounts.filter(a => a.last_export).length} 个账号已导出　<span>点击账号设置 Excel 字段，勾选账号参与导出</span></p></div><div className="head-actions">{loginResults && <button className="text-btn" onClick={() => setShowLoginResults(true)}>检测结果</button>}<button className="text-btn" disabled={busy} onClick={() => start('/check-logins', {})}><Icon name="refresh" size={15}/> 检测登录态</button><button className="primary-btn" disabled={busy} onClick={() => setAdding(true)}><Icon name="plus" size={16}/> 新增账号</button></div></div>
          <div className="workspace-columns">
            <section className="accounts-panel" aria-label="已配置账号">
              <div className="panel-title"><div><div className="section-index">CONNECTED ACCOUNTS</div><h2>已配置账号 <span>{state.accounts.length}</span></h2></div><div className="account-sort-controls"><label className="account-sort-label">排序<select aria-label="账号排序方式" value={state.account_sort.key} disabled={savingSort} onChange={e => changeSort({ key: e.target.value })}><option value="platform">平台</option><option value="connected_at">连接时间</option><option value="name">账号名称</option></select></label><button className="sort-direction" disabled={savingSort} aria-label={`当前${state.account_sort.direction === 'asc' ? '升序，切换为降序' : '降序，切换为升序'}`} onClick={() => changeSort({ direction: state.account_sort.direction === 'asc' ? 'desc' : 'asc' })}><Icon name={state.account_sort.direction === 'asc' ? 'ascending' : 'descending'} size={13}/>{state.account_sort.direction === 'asc' ? '升序' : '降序'}</button></div></div>
              <div className="account-list">
                {sortedAccounts.map(account => <div data-platform={account.platform} className="account-row" key={account.id}>
                  <button className="account-settings" onClick={() => openSettings(account)}>
                  <AccountAvatar account={account}/>
                  <span className="account-main"><strong>{account.name}</strong><small>{state.platforms[account.platform]?.name} · {platformMeta[account.platform].subtitle}</small></span>
                  <span className="account-export">{account.last_export ? `上次导出 ${account.last_export}` : '等待首次导出'}</span>
                  <span className={`account-state ${account.connection_status === 'expired' || account.connection_status === 'mismatch' ? 'expired-status' : account.connection_status === 'error' || account.connection_status === 'unchecked' ? 'unchecked-status' : ''}`} title={account.last_checked_at ? `${checkTime(account.last_checked_at)} · ${account.last_check_error || '登录有效，账号身份一致'}` : '尚无本次检测记录'}><i className="status-dot"/> {busy && job.kind === 'check-logins' ? '检测中…' : account.connection_status === 'connected' ? '上次验证通过' : loginStatusLabels[account.connection_status] || '待验证'}</span>
                  <Icon name="chevron" size={14}/>
                  </button>
                  <SelectionBox checked={!excludedIds.has(account.id)} disabled={busy} label={`导出账号 ${account.name}`} onChange={checked => selectAccounts([account.id], checked)}/>
                </div>)}
                {state.pending_sessions.map(session => <button data-platform={session.platform} className="account-row pending-row" key={`pending-${session.platform}`} onClick={() => { setChosenPlatform(session.platform); setAdding(true) }}>
                  <PlatformIcon platform={session.platform}/><span className="account-main"><strong>{state.platforms[session.platform]?.name}旧会话</strong><small>点击重新连接</small></span><span className="account-state pending-status"><i className="status-dot"/> 待验证</span><Icon name="chevron" size={14}/>
                </button>)}
                {!state.accounts.length && !state.pending_sessions.length && <div className="account-empty">还没有账号。点击右上角“新增账号”连接平台。</div>}
              </div>
            </section>
            <section className="destination-panel" aria-label="导出目的地">
              <div className="panel-title"><div><div className="section-index">EXPORT DESTINATION</div><h2>导出目的地</h2></div><span className="destination-emblem"><Icon name="folder" size={19}/></span></div>
              <p className="destination-description">仅采集已勾选账号，按平台分类，每个账号生成一份 Excel。</p>
              <label htmlFor="export-folder">本机文件夹</label>
              <div className="folder-input"><input id="export-folder" value={folder} readOnly title={folder} placeholder="尚未选择文件夹"/></div>
              <button className="destination-change" onClick={chooseFolder}><Icon name="folder" size={15}/> 更改目的地</button>
              <button className="export-button" disabled={busy || !selectedIds.length || !folderReady} onClick={() => start('/export', { folder, account_ids: selectedIds })}><span>采集并导出（{selectedIds.length}）</span><span className="export-button-icon"><Icon name="down" size={19}/></span></button>
              <div className={`export-hint ${folderReady && selectedIds.length ? '' : 'needs-folder'}`} role="status">{!selectedIds.length ? '请至少勾选一个账号' : !folder ? '请先选择保存文件夹' : !folderReady ? '上次选择的文件夹不可用，请重新选择' : 'Excel .xlsx · 实时采集，不导入本地数据'}</div>
            </section>
          </div>
        </> : <><div className="activity-intro"><div className="section-index">ACTIVITY</div><h1>导出记录<span className="title-dot">.</span></h1><p>这里显示本次运行的结果与各账号耗时。报告保存在你选择的文件夹中。</p></div>{results ? <div className="result-list">{results.map((r, i) => <div className={`result-item ${r.ok ? 'success' : 'failure'}`} key={i}><span className="result-icon">{r.ok ? <Icon name="check"/> : <Icon name="close"/>}</span><div><strong>{r.account}</strong><p>{r.ok ? `${r.count} 条作品 · ${r.path}` : r.error}</p></div><span className="result-state">{r.seconds} 秒 · {r.ok ? '已导出' : '失败'}</span></div>)}</div> : <div className="empty-activity">尚无本次导出记录。<button onClick={() => setTab('accounts')}>返回账号工作台 <Icon name="arrow" size={15}/></button></div>}</>}
      </div>
    </main>
    {showLoginResults && loginResults && <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) setShowLoginResults(false) }}><div className="modal login-results-modal" role="dialog" aria-modal="true" aria-labelledby="login-results-title"><button className="modal-close" aria-label="关闭检测结果" onClick={() => setShowLoginResults(false)}><Icon name="close"/></button><div className="modal-kicker">LOGIN STATUS</div><h2 id="login-results-title">登录检测结果<span>.</span></h2><p className="modal-intro">通过 {loginResults.filter(r => r.status === 'connected').length} · 过期 {loginResults.filter(r => r.status === 'expired').length} · 账号不一致 {loginResults.filter(r => r.status === 'mismatch').length} · 检测失败 {loginResults.filter(r => r.status === 'error').length}</p><div className="login-check-list">{loginResults.map((result, index) => <div className="login-check-item" key={index}><PlatformIcon platform={result.platform} size="small"/><div className="login-check-detail"><strong>{result.account}</strong><small>{state.platforms[result.platform]?.name} · {result.source === 'legacy' ? '旧会话' : '已有账号'} · {checkTime(result.checked_at)}</small><p>{result.message}</p></div><span className={`login-check-status status-${result.status}`}>{loginStatusLabels[result.status]}</span></div>)}</div>{!loginResults.length && <p className="modal-intro">没有可检测的账号或旧会话，请先新增账号。</p>}<button className="modal-action" onClick={() => setShowLoginResults(false)}>完成 <Icon name="check"/></button></div></div>}
    {adding && <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) setAdding(false) }}><div className="modal add-modal"><button className="modal-close" onClick={() => setAdding(false)}><Icon name="close"/></button><div className="modal-kicker">CONNECT / NEW ACCOUNT</div><h2>连接你的账号<span>.</span></h2><p className="modal-intro">选择平台后会打开它的登录网站。登录完成，账号会自动加入工作台。</p><div className="login-browser-info" role="status"><strong>登录浏览器{state.login_browser?.name ? ` · ${state.login_browser.name}` : ""}</strong><p>{state.login_browser?.message || "正在读取系统浏览器设置…"}</p><small>已有连接保留原登录浏览器；重新连接时使用当前默认或兼容浏览器。</small></div><div className="platform-options">{Object.entries(platformMeta).map(([key, meta]) => <button key={key} className={`platform-option ${chosenPlatform === key ? 'chosen' : ''}`} onClick={() => setChosenPlatform(key)}><PlatformIcon platform={key} size="small"/><span><strong>{state.platforms[key]?.name}</strong><small>{state.platforms[key]?.login}</small></span><span className="option-check">{chosenPlatform === key && <Icon name="check" size={15}/>}</span></button>)}</div>{chosenPlatform === 'xhs' && <div className="modal-tip">小红书现有脚本使用手机号和短信验证码登录，请在打开的浏览器窗口中完成。</div>}<button className="modal-action" disabled={busy || state.login_browser?.available === false} onClick={() => start('/accounts', { platform: chosenPlatform })}>打开网站并连接 <Icon name="arrow"/></button></div></div>}
    {activeAccount && draft && <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) setSelected(null) }}><div className="modal settings-modal"><button className="modal-close" onClick={() => setSelected(null)}><Icon name="close"/></button><div className="modal-kicker">ACCOUNT / EXPORT SETTINGS</div><div className="settings-identity"><PlatformIcon platform={activeAccount.platform}/><div><h2>{activeAccount.name}<span>.</span></h2><p>{state.platforms[activeAccount.platform]?.name} · ID {activeAccount.identity}{activeAccount.browser_name ? ` · 登录浏览器 ${activeAccount.browser_name}` : ""}</p></div></div><div className="settings-divider"/><div className="settings-label"><div><strong>Excel 导出字段</strong><p>字段来自当前平台采集脚本；采集不到的单元格留空。</p></div><span>{draft.fields.length} SELECTED</span></div><div className="field-grid">{(state.fields[activeAccount.platform] || []).map(field => <label className={`field-check ${draft.fields.includes(field.key) ? 'checked' : ''}`} key={field.key}><input type="checkbox" checked={draft.fields.includes(field.key)} onChange={() => setDraft(d => ({ ...d, fields: d.fields.includes(field.key) ? d.fields.filter(x => x !== field.key) : [...d.fields, field.key] }))}/><span className="box">{draft.fields.includes(field.key) && <Icon name="check" size={13}/>}</span><span>{field.label}</span>{available && !available.includes(field.key) && !['title','created'].includes(field.key) && <small>上次未获取</small>}</label>)}</div><div className="visibility-setting"><div><strong>包含仅自己可见 / 隐藏作品</strong><p>{activeAccount.platform === 'douyin' ? '按抖音审核状态筛选非公开作品。' : activeAccount.platform === 'xhs' ? '按小红书返回的笔记权限标记筛选。' : activeAccount.platform === 'channels' ? '按视频号可见范围筛选；缺少标记时会停止导出。' : 'B站公开空间列表已自动剔除私密稿件，无法采集私密稿件。'}</p></div><label className={`switch ${draft.include_private ? 'on' : ''} ${activeAccount.platform === 'bili' ? 'disabled' : ''}`}><input type="checkbox" disabled={activeAccount.platform === 'bili'} checked={draft.include_private} onChange={e => setDraft(d => ({ ...d, include_private: e.target.checked }))}/><span/></label></div><div className="settings-actions"><button className="secondary-btn" disabled={busy} onClick={() => start(`/accounts/${activeAccount.id}/reconnect`, {})}>重新连接</button><button className="danger-btn" disabled={busy} onClick={() => setDeleteConfirm(true)}>删除连接</button></div><button className="modal-action" disabled={!draft.fields.length || busy} onClick={saveSettings}>保存设置 <Icon name="check" size={18}/></button></div></div>}
    {deleteConfirm && activeAccount && <div className="modal-backdrop confirm-backdrop"><div className="modal confirm-modal"><h2>删除这个连接？</h2><p>将从工作台移除“{activeAccount.name}”，并清除这个连接保存的登录态。已导出的 Excel 不受影响。</p><div className="confirm-actions"><button className="secondary-btn" onClick={() => setDeleteConfirm(false)}>取消</button><button className="danger-btn" onClick={deleteConnection}>确认删除</button></div></div></div>}
    {busy && (job.kind === 'export' ? <div className="export-progress-panel"><div className="export-progress-top"><div><strong>正在采集并导出</strong><p>{job.message}</p></div><span>已处理 {progressCompleted}/{progressTotal} 个账号</span></div><div className="export-progress-track" role="progressbar" aria-label="导出进度" aria-valuemin={0} aria-valuemax={progressTotal} aria-valuenow={progressCompleted}><span className="export-progress-fill" style={{ width: `${progressPercent}%` }}/><span className="export-progress-scan"/></div></div> : <div className="job-toast"><span className="job-spinner"/><div><strong>{job.kind === 'login' ? '正在连接账号' : job.kind === 'reconnect' ? '正在重新连接账号' : '正在检测登录态'}</strong><p>{job.message}</p>{job.kind === 'check-logins' && job.total > 0 && <p>已检测 {job.completed || 0}/{job.total}</p>}</div></div>)}
    {notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice('')}><Icon name="close" size={16}/></button></div>}
  </div>
}
createRoot(document.getElementById('studio-root')).render(<App/>)
