"use client";

import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";

export type Language = "en" | "zh";
export type LocalizedText = { en: string; zh: string };

type LanguageContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  toggleLanguage: () => void;
  t: (english: string, chinese: string) => string;
  localize: (copy: LocalizedText) => string;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);
const LANGUAGE_PREFERENCE_KEY = "tmcra-language-preference";

function systemLanguage(): Language {
  return window.navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function LanguageProvider({ children, initialLanguage = "en" }: { children: ReactNode; initialLanguage?: Language }) {
  const [language, setLanguageState] = useState<Language>(initialLanguage);

  useEffect(() => {
    const stored = window.localStorage.getItem(LANGUAGE_PREFERENCE_KEY);
    const preferred: Language = stored === "zh" || stored === "en"
      ? stored
      : systemLanguage();
    const frame = window.requestAnimationFrame(() => setLanguageState(preferred));

    const followSystemLanguage = () => {
      const manualPreference = window.localStorage.getItem(LANGUAGE_PREFERENCE_KEY);
      if (manualPreference !== "zh" && manualPreference !== "en") {
        setLanguageState(systemLanguage());
      }
    };
    window.addEventListener("languagechange", followSystemLanguage);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("languagechange", followSystemLanguage);
    };
  }, []);

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);

  const setLanguage = useCallback((nextLanguage: Language) => {
    if (nextLanguage === language) return;

    window.localStorage.setItem(LANGUAGE_PREFERENCE_KEY, nextLanguage);

    const applyLanguage = () => flushSync(() => setLanguageState(nextLanguage));
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const transitionDocument = document as Document & {
      startViewTransition?: (update: () => void) => { finished: Promise<void> };
    };

    if (reduceMotion || !transitionDocument.startViewTransition) {
      document.documentElement.dataset.languageTransition = "fallback";
      applyLanguage();
      window.setTimeout(() => {
        delete document.documentElement.dataset.languageTransition;
      }, 220);
      return;
    }

    document.documentElement.dataset.languageTransition = "active";
    transitionDocument.startViewTransition(applyLanguage).finished.finally(() => {
      delete document.documentElement.dataset.languageTransition;
    });
  }, [language]);

  const t = useCallback((english: string, chinese: string) => language === "zh" ? chinese : english, [language]);
  const localize = useCallback((copy: LocalizedText) => copy[language], [language]);
  const toggleLanguage = useCallback(() => setLanguage(language === "en" ? "zh" : "en"), [language, setLanguage]);
  const value = useMemo(() => ({ language, setLanguage, toggleLanguage, t, localize }), [language, localize, setLanguage, t, toggleLanguage]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) throw new Error("useLanguage must be used inside LanguageProvider");
  return context;
}

export function LanguageToggle({ className = "" }: { className?: string }) {
  const { language, t, toggleLanguage } = useLanguage();
  return (
    <button
      className={`language-toggle ${className}`.trim()}
      type="button"
      aria-label={t("Switch interface language to Chinese", "将界面切换为英文")}
      onClick={toggleLanguage}
    >
      <span className={language === "en" ? "is-active" : ""}>EN</span>
      <i aria-hidden="true">/</i>
      <span className={language === "zh" ? "is-active" : ""}>中文</span>
    </button>
  );
}
