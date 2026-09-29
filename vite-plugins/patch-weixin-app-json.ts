import type { Plugin } from 'vite'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

export function patchWeixinAppJsonPlugin(): Plugin {
  return {
    name: 'patch-weixin-app-json',
    apply: 'build',
    enforce: 'post',
    writeBundle: {
      order: 'post',
      handler() {
        if (process.env.UNI_PLATFORM !== 'mp-weixin') {
          return
        }

        const modeDir = process.env.NODE_ENV === 'production' ? 'build' : 'dev'
        const appJsonPath = path.resolve(process.cwd(), `dist/${modeDir}/mp-weixin/app.json`)

        if (!fs.existsSync(appJsonPath)) {
          return
        }

        const appJson = JSON.parse(fs.readFileSync(appJsonPath, 'utf8'))

        // uni-app 当前构建链路可能不会把 manifest 里的该字段透传到 app.json，这里兜底写入。
        appJson.requiredBackgroundModes = Array.from(
          new Set([...(appJson.requiredBackgroundModes || []), 'audio']),
        )

        fs.writeFileSync(appJsonPath, `${JSON.stringify(appJson, null, 2)}\n`)
      },
    },
  }
}
