import { useCallback, useEffect, useState } from "react";
import { detectLanguage, detectLanguageNow } from "../i18n/detect";
import { setLanguage } from "../i18n";
import type { Lang } from "../i18n";

/** Resolves the UI language once per panel mount; `reread` asks Steam again (e.g. after a language change). */
export function useLanguage() {
  const [lang, setLang] = useState<Lang>(() => {
    const guess = detectLanguageNow();
    setLanguage(guess);
    return guess;
  });

  const reread = useCallback(async () => {
    const found = await detectLanguage();
    setLanguage(found);
    setLang(found);
  }, []);

  useEffect(() => {
    void reread();
  }, [reread]);

  return { lang, reread };
}
