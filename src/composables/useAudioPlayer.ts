import { computed, reactive, watch } from 'vue'
import { BUILD_STAMP } from '@/utils/buildInfo'

export interface AudioPlayerState {
  src: string
  currentTime: number
  duration: number
  playing: boolean
  loading: boolean
  started: boolean
  error: string
  /** 缓冲中的提示，例如「缓冲 42%」，播通之后清空 */
  hint: string
}

export interface PlaylistItem {
  id: string
  title: string
  audioUrl: string
  duration?: number
}

interface AudioEngine {
  src: string
  title?: string
  epname?: string
  singer?: string
  coverImgUrl?: string
  duration: number
  currentTime: number
  paused?: boolean
  play: () => void
  pause: () => void
  stop?: () => void
  seek: (position: number) => void
  destroy?: () => void
  onCanplay: (callback: () => void) => void
  onPlay: (callback: () => void) => void
  onPause: (callback: () => void) => void
  onStop?: (callback: () => void) => void
  onEnded: (callback: () => void) => void
  onTimeUpdate: (callback: () => void) => void
  onWaiting?: (callback: () => void) => void
  onError: (callback: (error?: unknown) => void) => void
}

const TAG = '[Audio]'
const CACHE_KEY = 'jy_audio_files'
const CACHE_DIR_NAME = 'jy-audio'
/** 小程序本地文件配额 200MB，留足余量 */
const CACHE_BUDGET = 120 * 1024 * 1024

// 播放状态放到模块级别，页面销毁后后台音频仍能继续驱动同一份状态。
const state = reactive<AudioPlayerState>({
  src: '',
  currentTime: 0,
  duration: 0,
  playing: false,
  loading: false,
  started: false,
  error: '',
  hint: '',
})

let audio: AudioEngine | null = null
/** 真正交给播放器的地址，可能是本地临时文件 */
let engineSrc = ''
/** 数据里那条云端直链，页面用它比对「当前文章在不在播」 */
let wantedSrc = ''
let wantedTitle = ''
let autoPlayOnSrc = false

/**
 * 起播档位，一档不通自动退下一档，退到通了为止。
 * bg-local    背景音频 + 本地文件：能锁屏续听，所以排在第一档
 * inner-local 前台播放器 + 本地文件：先保证有声音，放弃锁屏
 * inner-http  前台播放器 + 云端直链：怀疑落盘有问题时的最后一搏
 */
const STAGES = ['bg-local', 'inner-local', 'inner-http'] as const
let stageIdx = 0
/** 当前交给播放器的是本地文件还是直链 */
let playingLocal = ''
/** 这次本地文件是复用旧缓存（缓存坏了要重下）还是刚下下来的 */
let localWasCache = false
/** 已经因为「缓存文件坏了」重下过一次的那篇 */
let refetchedFor = ''
/** 交给播放器之后迟迟不 canplay，就当成失败换档 */
let watchdog: ReturnType<typeof setTimeout> | null = null
let inflight: Promise<string> | null = null
let inflightFor = ''

interface CacheRec {
  path: string
  bytes: number
  at: number
}

function loadCache(): Record<string, CacheRec> {
  try {
    const raw = uni.getStorageSync(CACHE_KEY)
    const obj = typeof raw === 'string' ? JSON.parse(raw || '{}') : raw
    return obj && typeof obj === 'object' ? obj as Record<string, CacheRec> : {}
  }
  catch {
    return {}
  }
}

const cache: Record<string, CacheRec> = loadCache()

function saveCache() {
  try {
    uni.setStorageSync(CACHE_KEY, JSON.stringify(cache))
  }
  catch {
    // 存储写不进去不影响听音频
  }
}

function wxApi(): any {
  return typeof wx !== 'undefined' ? wx : null
}

function fileSystem(): any {
  return wxApi()?.getFileSystemManager?.() ?? null
}

function userDir(): string {
  return wxApi()?.env?.USER_DATA_PATH || (uni as any)?.env?.USER_DATA_PATH || ''
}

function pathExists(path: string) {
  const m = fileSystem()
  if (!m || !path) {
    return false
  }
  try {
    m.accessSync(path)
    return true
  }
  catch {
    return false
  }
}

/** 每条链接一个稳定文件名，换地址就换文件 */
function fileNameFor(url: string) {
  let h = 5381
  for (let i = 0; i < url.length; i++) {
    h = ((h << 5) + h + url.charCodeAt(i)) | 0
  }
  const tail = (url.split('/').pop() || 'audio').split('?')[0]
  return `${(h >>> 0).toString(36)}-${tail}`
}

function cachedPath(url: string) {
  const rec = cache[url]
  if (!rec) {
    return ''
  }
  if (!pathExists(rec.path)) {
    delete cache[url]
    saveCache()
    return ''
  }
  rec.at = Date.now()
  saveCache()
  return rec.path
}

function dropCache(url: string) {
  const rec = cache[url]
  if (!rec) {
    return
  }
  try {
    fileSystem()?.unlinkSync(rec.path)
  }
  catch {
    // 文件已经不在了就算了
  }
  delete cache[url]
  saveCache()
}

function trimCache() {
  const entries = Object.entries(cache)
  let total = entries.reduce((sum, rec) => sum + (rec[1].bytes || 0), 0)
  if (total <= CACHE_BUDGET) {
    return
  }
  entries.sort((a, b) => a[1].at - b[1].at)
  for (const [url, rec] of entries) {
    if (url === wantedSrc || url === inflightFor) {
      continue
    }
    try {
      fileSystem()?.unlinkSync(rec.path)
    }
    catch {
      // 删不掉就只清记录
    }
    delete cache[url]
    total -= rec.bytes || 0
    if (total <= CACHE_BUDGET) {
      break
    }
  }
  saveCache()
}

/** 临时文件挪进用户目录，下次点开这篇就能秒开 */
function persist(tempPath: string, url: string) {
  const base = userDir()
  const m = fileSystem()
  if (!base || !m) {
    return tempPath
  }
  const dir = `${base}/${CACHE_DIR_NAME}`
  if (!pathExists(dir)) {
    try {
      m.mkdirSync(dir, true)
    }
    catch {
      return tempPath
    }
  }
  const dest = `${dir}/${fileNameFor(url)}`
  try {
    if (pathExists(dest)) {
      m.unlinkSync(dest)
    }
    m.copyFileSync(tempPath, dest)
    const size = Number(m.statSync(dest)?.size || 0)
    cache[url] = { path: dest, bytes: size, at: Date.now() }
    saveCache()
    trimCache()
    try {
      m.unlinkSync(tempPath)
    }
    catch {
      // 临时文件由微信回收
    }
    return dest
  }
  catch {
    return tempPath
  }
}

// ── 播放列表（模块级，跨页面持久） ──
const playlistState = reactive({
  playlist: [] as PlaylistItem[],
  currentIndex: -1,
})

const hasNext = computed(() => {
  return playlistState.currentIndex >= 0
    && playlistState.currentIndex < playlistState.playlist.length - 1
})

const hasPrev = computed(() => {
  return playlistState.currentIndex > 0
})

const currentPlaylistItem = computed(() => {
  const i = playlistState.currentIndex
  if (i >= 0 && i < playlistState.playlist.length) {
    return playlistState.playlist[i]
  }
  return null
})

const progress = computed(() => {
  if (!state.duration) {
    return 0
  }
  return Math.min(100, Math.max(0, (state.currentTime / state.duration) * 100))
})

function cut(url: string) {
  return (url || '').slice(0, 70)
}

/** 微信给的原因是一长串 errMsg，取错误码出来才看得懂 */
function describeError(error: unknown) {
  const e = (error || {}) as Record<string, any>
  const raw = typeof error === 'string' ? error : String(e.errMsg || e.message || '')
  const fromText = (raw.match(/errCode[:=]\s*(-?\d+)/) || [])[1]
  const code = e.errCode != null ? String(e.errCode) : (fromText || '')
  const text = raw.replace(/errCode[:=]\s*-?\d+,?\s*/g, '').replace(/^\s*err:?\s*/i, '').trim()
  if (code) {
    return `错误码 ${code}${text ? ` ${text.slice(0, 34)}` : ''}`
  }
  return text.slice(0, 44) || '未知错误'
}

function stageName() {
  return STAGES[stageIdx]
}

/** 第一档才试背景音频 */
function wantBackground() {
  return stageIdx === 0
}

/** 最后一档不落地文件，直接喂直链 */
function wantLocalFile() {
  return stageIdx < STAGES.length - 1
}

let audioOptionDone = false

/** 全局播放选项，只对前台播放器生效 */
function applyAudioOptions() {
  const api = wxApi()
  if (audioOptionDone || !api?.setInnerAudioOption) {
    return
  }
  audioOptionDone = true
  try {
    // iOS 侧边静音键按下时模拟器照样有声、真机一声不出，这里强制出声
    api.setInnerAudioOption({ obeyMuteSwitch: false })
  }
  catch {
    // 老基础库没这个接口，跳过
  }
}

function createEngine(): AudioEngine {
  // 微信小程序优先用官方背景音频管理器，退出微信还能继续听。
  if (wantBackground() && typeof wx !== 'undefined' && typeof wx.getBackgroundAudioManager === 'function') {
    autoPlayOnSrc = true
    console.log(TAG, `build=${BUILD_STAMP} 档=${stageName()} 引擎=背景音频`)
    return wx.getBackgroundAudioManager() as unknown as AudioEngine
  }
  applyAudioOptions()
  autoPlayOnSrc = false
  console.log(TAG, `build=${BUILD_STAMP} 档=${stageName()} 引擎=前台音频`)
  const engine: any = uni.createInnerAudioContext()
  try {
    engine.obeyMuteSwitch = false
  }
  catch {
    // 个别基础库把它做成了只读，跳过
  }
  return engine as AudioEngine
}

/** 落盘的文件到底是不是音频，真机上排查一次就够 */
function logLocalFile(path: string) {
  const m = fileSystem()
  if (!m) {
    return
  }
  try {
    const bytes = Number(m.statSync(path)?.size || 0)
    const head = new Uint8Array(m.readFileSync(path, undefined, 0, 3) as ArrayBuffer)
    const magic = head.length === 3 ? Array.from(head).map(b => b.toString(16).padStart(2, '0')).join('') : '?'
    console.log(TAG, `本地文件 ${(bytes / 1048576).toFixed(1)}MB 头=${magic}`)
  }
  catch {
    // 打不出来不影响播放
  }
}

function applySrc(engine: AudioEngine, src: string, title: string) {
  engineSrc = src
  // 背景音频要求先给 title 再给 src，缺 title 时部分安卓机型直接拒绝起播。
  engine.title = title || '听文章'
  engine.epname = '九哥隐学'
  engine.singer = '九哥'
  engine.src = src
  if (!autoPlayOnSrc) {
    engine.play()
  }
  armWatchdog(src)
}

/**
 * 先把整篇音频落成本地文件，再交给播放器。
 *
 * 支付宝云存储的下载域名对任何 GET 都只回 Transfer-Encoding: chunked，
 * 连 Range 都不给 Content-Length（HEAD 才有）；开发者工具和手机浏览器
 * 自己会边下边猜所以听得见，微信真机的音频内核拿不到长度，
 * 在初始化解码器那一步就失败，只弹「当前内容无法播放」。
 * downloadFile 走微信自己的下载器，能吃下这种流式响应。
 */
function bufferToTemp(url: string, silent = false): Promise<string> {
  if (inflight && inflightFor === url) {
    return inflight
  }
  inflightFor = url
  inflight = new Promise<string>((resolve, reject) => {
    const task = uni.downloadFile({
      url,
      timeout: 300000,
      success: res => (res.statusCode === 200 && res.tempFilePath
        ? resolve(persist(res.tempFilePath, url))
        : reject(new Error(`HTTP ${res.statusCode}`))),
      fail: err => reject(new Error(String((err as any)?.errMsg || '下载失败'))),
    })
    ;(task as any)?.onProgressUpdate?.((res: any) => {
      const pct = Number(res?.progress || 0)
      if (!silent && pct > 0 && wantedSrc === url) {
        state.hint = `缓冲 ${Math.min(99, Math.round(pct))}%`
      }
    })
  }).finally(() => {
    inflight = null
    inflightFor = ''
  })
  return inflight
}

/** 播通之后悄悄把下一集也拉下来，连着一集一集就不用等 */
function warmNext() {
  // 最后一档走的是直链，落盘预取没意义，别白耗流量
  if (!hasNext.value || !wantLocalFile()) {
    return
  }
  const next = playlistState.playlist[playlistState.currentIndex + 1]
  const url = next?.audioUrl
  if (!url || url === wantedSrc || inflight || cachedPath(url)) {
    return
  }
  bufferToTemp(url, true).catch(() => {})
}

function clearWatchdog() {
  if (watchdog) {
    clearTimeout(watchdog)
    watchdog = null
  }
}

/**
 * 微信不一定每次都抛 onError，安卓上更多是直接沉默。
 * 所以把「多久之内必须 canplay」也当成一次失败来处理，超时立刻退档。
 */
function armWatchdog(target: string) {
  clearWatchdog()
  const want = wantedSrc
  const ms = target.startsWith('http') ? 15000 : 10000
  watchdog = setTimeout(() => {
    watchdog = null
    if (want !== wantedSrc || state.playing) {
      return
    }
    console.log(TAG, '起播超时', target.startsWith('http') ? '直链' : '本地文件')
    recover('起播超时')
  }, ms)
}

function createAudio(): AudioEngine {
  const engine = createEngine()

  engine.onCanplay(() => {
    clearWatchdog()
    state.loading = false
    if (!inflight) {
      state.hint = ''
    }
    if (engine.duration > 0) {
      state.duration = engine.duration
    }
    console.log(TAG, 'canplay', cut(engineSrc))
  })

  engine.onPlay(() => {
    clearWatchdog()
    state.playing = true
    state.loading = false
    state.error = ''
    state.hint = ''
    warmNext()
  })

  engine.onPause(() => {
    state.playing = false
    state.loading = false
  })

  engine.onStop?.(() => {
    state.playing = false
    state.currentTime = 0
    state.loading = false
  })

  engine.onEnded(() => {
    state.playing = false
    state.currentTime = state.duration
    state.loading = false

    // 自动播下一集
    if (hasNext.value) {
      setTimeout(() => playNext(), 500)
    }
  })

  engine.onTimeUpdate(() => {
    state.currentTime = engine.currentTime

    if (engine.duration > 0) {
      state.duration = engine.duration
    }
  })

  engine.onWaiting?.(() => {
    state.loading = true
  })

  engine.onError((error?: unknown) => {
    recover(describeError(error))
  })

  return engine
}

function ensureAudio(): AudioEngine {
  if (!audio) {
    audio = createAudio()
  }
  return audio
}

function resetState() {
  state.src = ''
  state.currentTime = 0
  state.duration = 0
  state.playing = false
  state.loading = false
  state.started = false
  state.error = ''
  state.hint = ''
}

function syncState(src: string) {
  if (!audio || wantedSrc !== src) {
    resetState()
    return
  }

  state.started = true
  state.src = wantedSrc
  state.playing = audio.paused === false
  state.loading = false
  state.currentTime = audio.currentTime || 0
  state.duration = audio.duration || 0
}

/**
 * 起播失败或超时都走这里：先怀疑缓存文件坏掉，再一档一档往后退。
 * 档位是模块级的，一旦摸清这台机器哪档能用，后面每篇都直接用它。
 */
function recover(why: string) {
  clearWatchdog()
  const want = wantedSrc
  if (!want) {
    return
  }

  // 文件还没交出去之前，播放器回调一律不理，免得抢跑换档
  if (!engineSrc) {
    return
  }

  // 旧的缓存文件可能下坏了，扔掉重下一遍，还留在当前档
  if (playingLocal && localWasCache && refetchedFor !== want) {
    refetchedFor = want
    console.log(TAG, '缓存文件不可用，重新下载', why)
    dropCache(want)
    state.loading = true
    bufferToTemp(want)
      .then((file) => {
        if (want !== wantedSrc) {
          return
        }
        localWasCache = false
        applySrc(ensureAudio(), file, wantedTitle)
      })
      .catch(err => failWith(err))
    return
  }

  if (stageIdx < STAGES.length - 1) {
    stageIdx++
    console.log(TAG, `${why}，退到第 ${stageIdx + 1} 档 ${stageName()}`)
    releaseAudio()
    start(want, wantedTitle)
    return
  }

  failWith(why)
}

function releaseAudio() {
  if (audio?.destroy) {
    audio.destroy()
  }
  else {
    audio?.stop?.()
  }

  clearWatchdog()
  audio = null
  engineSrc = ''
  playingLocal = ''
  resetState()
}

/** 全部失败才落到这里，错误原因带上微信给的错误码 */
function failWith(err: unknown) {
  const why = typeof err === 'string' ? err : describeError(err)
  console.log(TAG, '失败', why, '| 档', stageName())
  state.playing = false
  state.loading = false
  state.hint = ''
  state.error = `音频加载失败（${why}）`
}

function attach(target: string) {
  playingLocal = target.startsWith('http') ? '' : target
  if (!inflight) {
    state.hint = ''
  }

  const engine = ensureAudio()
  if (engineSrc === target) {
    engine.play()
    armWatchdog(target)
    return
  }
  applySrc(engine, target, wantedTitle)
}

/**
 * 起播。
 *
 * 小程序里先取本地缓存，没有就先下载；真机的音频内核放不了
 * 支付宝云那种没有 Content-Length 的流式响应，只能先把文件落盘。
 * 浏览器等非小程序端仍然用直链，边下边听更快。
 */
function start(src: string, title: string) {
  wantedSrc = src
  wantedTitle = title
  refetchedFor = ''
  state.src = src
  state.error = ''
  state.started = true
  state.loading = true
  state.currentTime = 0
  state.duration = 0

  // 先把播放器建出来，占住这次点击解锁的音频会话，再去做下载。
  // iOS 只认用户手势里发起的播放，下载完才 create 容易被系统拦下不出声。
  ensureAudio()

  if (!wantLocalFile()) {
    localWasCache = false
    attach(src)
    return
  }

  const local = cachedPath(src)
  if (local) {
    localWasCache = true
    logLocalFile(local)
    attach(local)
    return
  }

  localWasCache = false

  if (fileSystem()) {
    bufferToTemp(src)
      .then(file => (src === wantedSrc ? attach(file) : undefined))
      .catch(err => failWith(err))
    return
  }

  attach(src)
}

function play(getSrc: () => string, getTitle: () => string) {
  const src = getSrc()

  if (!src) {
    state.error = '这篇文章暂时没有音频。'

    // 没有音频的条目直接跳过，接着往下听
    if (playlistState.playlist.length > 0 && hasNext.value) {
      setTimeout(() => playNext(), 300)
    }
    return
  }

  start(src, getTitle())
}

export function useAudioPlayerState() {
  return state
}

export function usePlaylistState() {
  return { playlistState, hasNext, hasPrev, currentPlaylistItem }
}

// ── 播放列表控制 ──
export function setPlaylist(items: PlaylistItem[], startIndex: number = 0) {
  playlistState.playlist = items
  playlistState.currentIndex = startIndex
}

export function playNext() {
  if (!hasNext.value)
    return
  const nextIndex = playlistState.currentIndex + 1
  const item = playlistState.playlist[nextIndex]
  playlistState.currentIndex = nextIndex
  play(() => item.audioUrl, () => item.title)
}

export function playPrev() {
  if (!hasPrev.value)
    return
  const prevIndex = playlistState.currentIndex - 1
  const item = playlistState.playlist[prevIndex]
  playlistState.currentIndex = prevIndex
  play(() => item.audioUrl, () => item.title)
}

export function playByIndex(index: number) {
  if (index < 0 || index >= playlistState.playlist.length)
    return
  const item = playlistState.playlist[index]
  playlistState.currentIndex = index
  play(() => item.audioUrl, () => item.title)
}

export function useAudioPlayer(getSrc: () => string, getTitle: () => string = () => '') {
  watch(getSrc, (src) => {
    syncState(src)
  }, { immediate: true })

  return {
    state,
    progress,
    play: () => play(getSrc, getTitle),
    pause: () => audio?.pause(),
    toggle: () => {
      if (state.playing) {
        audio?.pause()
        return
      }
      play(getSrc, getTitle)
    },
    seek: (value: number) => {
      if (!audio || !state.duration) {
        return
      }

      const time = Math.min(state.duration, Math.max(0, value))
      audio.seek(time)
      state.currentTime = time
    },
    destroy: releaseAudio,
  }
}

export function useAudioPlayerWithPlaylist() {
  return {
    state,
    progress,
    hasNext,
    hasPrev,
    currentPlaylistItem,
    play: () => {
      const item = currentPlaylistItem.value
      if (item) {
        play(() => item.audioUrl, () => item.title)
      }
    },
    pause: () => audio?.pause?.(),
    toggle: () => {
      if (state.playing && state.started) {
        audio?.pause?.()
        return
      }
      const item = currentPlaylistItem.value
      if (item) {
        play(() => item.audioUrl, () => item.title)
      }
    },
    playNext,
    playPrev,
    playByIndex,
    seek: (value: number) => {
      if (!audio || !state.duration)
        return
      const time = Math.min(state.duration, Math.max(0, value))
      audio.seek(time)
      state.currentTime = time
    },
    destroy: releaseAudio,
  }
}

/** 直接起播：传入音频地址和标题，不依赖播放列表 */
export function playAudioDirect(src: string, title: string) {
  if (!src)
    return
  play(() => src, () => title)
}
