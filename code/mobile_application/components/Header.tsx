import React from 'react';
import { View, Text, StyleSheet, Platform } from 'react-native';
import { useTranslation } from 'react-i18next';

interface HeaderProps {
  currentPage: number;
}

const Header: React.FC<HeaderProps> = ({ currentPage }) => {
  const { t } = useTranslation();

  const getPageTitleKey = () => {
    switch (currentPage) {
      case 1: return 'pages.map';
      case 2: return 'pages.sos';
      case 3: return 'pages.settings';
      case 4: return 'pages.quickStart';
      case 5: return 'pages.booking';
      case 6: return 'pages.profile';
      case 7: return 'pages.health';
      default: return 'pages.map';
    }
  };

  return (
    <View style={styles.header}>
      <Text style={styles.title}>{t(getPageTitleKey())}</Text>
    </View>
  );
};

const styles = StyleSheet.create({
  header: {
    height: Platform.OS === 'ios' ? 130 : 100,
    justifyContent: 'flex-end',
    alignItems: 'center',
    backgroundColor: '#ffffff',
    borderBottomWidth: 1,
    borderBottomColor: '#e0e0e0',
    paddingBottom: 10,
  },
  title: {
    fontSize: 22,
    fontWeight: '700',
    color: '#111827',
  },
});

export default Header;
