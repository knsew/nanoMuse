/**
 * About nanoMuse and the version (C2). The installed line reads
 * "nanoMuse Desktop <app> · harness <bundle>": the app's number from the Electron
 * shell (`nanomuse:info`), the bundle's from package.json at build time. There is
 * no update check: a new release is fetched by hand, from the releases page.
 */
import { createElement as h, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { type Translate } from './api.ts'
import { bridge, openLink } from './bridge.ts'
import { openStar } from './AccountPage.tsx'
import { DOCS_URL, ISSUES_URL, REPO_URL } from './panels.ts'
import { Sheet } from './ui.tsx'

export const RELEASES_URL = 'https://github.com/knsew/nanoMuse/releases'

interface AppInfo {
  version: string
  platform: string
  arch: string
  /** The Linux build runs from an AppImage (shell 1.0.0); absent from older shells. */
  appImage?: boolean
}

let appInfo: AppInfo | undefined
let appInfoAsked = false
const infoListeners = new Set<() => void>()

/** The shell's version, platform and arch; undefined in a plain browser or until the shell answers. */
export function useAppInfo(): AppInfo | undefined {
  return useSyncExternalStore(
    (l) => {
      infoListeners.add(l)
      if (!appInfoAsked) {
        appInfoAsked = true
        void bridge()?.info().then((i) => { appInfo = i; for (const x of infoListeners) x() }).catch(() => undefined)
      }
      return () => infoListeners.delete(l)
    },
    () => appInfo,
    () => appInfo,
  )
}

/** "nanoMuse Desktop 0.1.34 · harness 0.1.34" under the shell; the bundle alone in a browser. */
export function versionLine(t: Translate, bundle: string, app: AppInfo | undefined): string {
  return app ? t('abVersionLine', { app: app.version || '—', bundle: bundle || '—' }) : t('abVersionBundle', { bundle: bundle || '—' })
}

/** The About sheet's open/close, for the ••• menu. */
export const aboutBus: { open?: (() => void) | undefined } = {}

/**
 * The version (C2), one line that is always there: what is installed
 * ("nanoMuse Desktop 0.1.34 · harness 0.1.34").
 */
export function UpdateRow({ t, bundle }: { t: Translate; bundle: string }): ReactNode {
  const app = useAppInfo()
  const installed = app ? t('vrInstalled', { app: app.version || '—', bundle: bundle || '—' }) : t('abVersionBundle', { bundle: bundle || '—' })
  return h('div', { className: 'nm-row nm-version-row' },
    h('div', { className: 'nm-row-main' },
      h('span', { className: 'nm-row-title' }, t('vrTitle')),
      h('span', { className: 'nm-row-sub nm-version-line' }, installed)))
}

export function makeAboutSheet(t: Translate, bundle: string) {
  return function AboutSheet(): ReactNode {
    const [open, setOpen] = useState(false)
    useEffect(() => {
      aboutBus.open = () => setOpen(true)
      return () => { aboutBus.open = undefined }
    }, [])
    if (!open) return null
    return h(Sheet, { title: t('aboutTitle'), onClose: () => setOpen(false), closeLabel: t('close') },
      h('p', { className: 'nm-fine' }, t('abLead')),
      h('div', { className: 'nm-card' },
        h(UpdateRow, { t, bundle }),
        h('div', { className: 'nm-row' },
          h('div', { className: 'nm-row-main' },
            h('span', { className: 'nm-row-title' }, t('legalLicense')),
            h('span', { className: 'nm-row-sub nm-wrap' }, t('legalLicenseText'))))),
      h('div', { className: 'nm-card' },
        // going to GitHub from here counts as the star being done: the asks stop (C1)
        link(t('gnStar'), REPO_URL, () => openStar(REPO_URL)),
        link(t('abReleases'), RELEASES_URL),
        link(t('abDocs'), DOCS_URL),
        link(t('abIssues'), ISSUES_URL)))
  }
}

function link(label: string, url: string, onClick: () => void = () => openLink(url)): ReactNode {
  return h('button', { type: 'button', className: 'nm-row nm-row-button', onClick },
    h('div', { className: 'nm-row-main' }, h('span', { className: 'nm-row-title' }, label), h('span', { className: 'nm-row-sub' }, url.replace(/^https:\/\//, ''))))
}
