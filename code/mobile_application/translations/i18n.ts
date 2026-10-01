import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import enTranslations from './en/translations.json';
import zhTranslations from './zh/translations.json';

i18next
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: enTranslations },
      zh: { translation: zhTranslations },
    },


    lng: 'zh',
    fallbackLng: 'en',
    interpolation: {
      escapeValue: false,
    },
  });

export default i18next;
