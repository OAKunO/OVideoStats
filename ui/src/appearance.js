export const appearanceOptions = [
  { value: 'light', label: '奶油白', icon: 'sun' },
  { value: 'dark', label: '暖夜黑', icon: 'moon' },
  { value: 'system', label: '跟随系统', icon: 'system' },
]

export function normalizeAppearance(value) {
  return appearanceOptions.some(option => option.value === value) ? value : 'light'
}

export function resolveTheme(appearance, systemTheme) {
  return appearance === 'system' ? (systemTheme === 'dark' ? 'dark' : 'light') : normalizeAppearance(appearance)
}

export function themeBackground(theme) {
  return theme === 'dark' ? '#201f1d' : '#f8f5ef'
}
