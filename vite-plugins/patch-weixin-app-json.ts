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
        fs.writeFileSync(appJsonPath, `${JSON.stringify(appJson, null, 2)}\n`)
      },
    },
  }
}
