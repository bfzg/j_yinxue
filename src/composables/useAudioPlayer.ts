import { computed, reactive, watch } from 'vue'

export interface AudioPlayerState {
  src: string
  currentTime: number
  duration: number
  playing: boolean
  loading: boolean
  started: boolean
  error: string
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

// 播放状态放到模块级别，页面销毁后后台音频仍能继续驱动同一份状态。
const state = reactive<AudioPlayerState>({
  src: '',
  currentTime: 0,
  duration: 0,
  playing: false,
  loading: false,
  started: false,
  error: '',
})

let audio: AudioEngine | null = null
let currentSrc = ''
let autoPlayOnSrc = false

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

function createEngine() {
  // 微信小程序必须使用官方背景音频管理器，退出微信后才能继续播放。
  if (typeof wx !== 'undefined' && typeof wx.getBackgroundAudioManager === 'function') {
    autoPlayOnSrc = true
    return wx.getBackgroundAudioManager() as unknown as AudioEngine
  }

  autoPlayOnSrc = false
  return uni.createInnerAudioContext() as unknown as AudioEngine
}

function createAudio() {
  const engine = createEngine()

  engine.onCanplay(() => {
    state.loading = false
    if (engine.duration > 0) {
      state.duration = engine.duration
    }
  })

  engine.onPlay(() => {
    state.playing = true
    state.loading = false
    state.error = ''
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

    // 自动播放下一首
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

  engine.onError(() => {
    state.playing = false
    state.loading = false
    state.error = '音频加载失败，请稍后再试。'
  })

  return engine
}

function ensureAudio() {
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
}

function syncState(src: string) {
  if (!audio || currentSrc !== src) {
    resetState()
    return
  }

  state.started = true
  state.src = currentSrc
  state.playing = audio.paused === false
  state.loading = false
  state.currentTime = audio.currentTime || 0
  state.duration = audio.duration || 0
}

function releaseAudio() {
  if (audio?.destroy) {
    audio.destroy()
  }
  else {
    audio?.stop?.()
  }

  audio = null
  currentSrc = ''
  resetState()
}

function play(getSrc: () => string, getTitle: () => string) {
  const src = getSrc()

  if (!src) {
    state.error = '这篇文章暂时没有音频。'

    // 跳过无音频条目，自动播下一首
    if (playlistState.playlist.length > 0 && hasNext.value) {
      setTimeout(() => playNext(), 300)
    }
    return
  }

  if (audio && currentSrc !== src) {
    releaseAudio()
  }

  const engine = ensureAudio()

  if (currentSrc !== src) {
    engine.title = getTitle() || '听文章'
    engine.epname = '九哥隐学'
    engine.singer = '九哥'
    // 微信 BackgroundAudioManager 设置 src 后会自动播放；再调用 play 可能触发参数异常。
    engine.src = src
    currentSrc = src
    state.src = src
    state.currentTime = 0
    state.duration = 0

    if (!autoPlayOnSrc) {
      engine.play()
    }
  }
  else {
    engine.play()
  }

  state.error = ''
  state.loading = true
  state.started = true
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
  if (!hasNext.value) return
  const nextIndex = playlistState.currentIndex + 1
  const item = playlistState.playlist[nextIndex]
  playlistState.currentIndex = nextIndex
  currentSrc = ''
  play(() => item.audioUrl, () => item.title)
}

export function playPrev() {
  if (!hasPrev.value) return
  const prevIndex = playlistState.currentIndex - 1
  const item = playlistState.playlist[prevIndex]
  playlistState.currentIndex = prevIndex
  currentSrc = ''
  play(() => item.audioUrl, () => item.title)
}

export function playByIndex(index: number) {
  if (index < 0 || index >= playlistState.playlist.length) return
  const item = playlistState.playlist[index]
  playlistState.currentIndex = index
  currentSrc = ''
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
        currentSrc = ''
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
        currentSrc = ''
        play(() => item.audioUrl, () => item.title)
      }
    },
    playNext,
    playPrev,
    playByIndex,
    seek: (value: number) => {
      if (!audio || !state.duration) return
      const time = Math.min(state.duration, Math.max(0, value))
      audio.seek(time)
      state.currentTime = time
    },
    destroy: releaseAudio,
  }
}
