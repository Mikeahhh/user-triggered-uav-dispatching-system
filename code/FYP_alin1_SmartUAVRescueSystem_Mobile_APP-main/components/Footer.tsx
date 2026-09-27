import React from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Platform,
} from 'react-native';
import { useTranslation } from 'react-i18next';

interface FooterProps {
  currentPage: number;
  onSelectPage: (page: number) => void;
}

const Footer: React.FC<FooterProps> = ({ currentPage, onSelectPage }) => {
  const { t } = useTranslation();

  const tabs = [
    {
      id: 1,
      label: t('footer.map') || '地圖',
      emoji: '📍',
    },
    {
      id: 2,
      label: 'SOS',
      isSOS: true,
    },
    {
      id: 3,
      label: t('footer.settings') || '設定',
      emoji: '⚙️',
    },
  ];

  return (
    <View style={styles.footer}>
      {tabs.map(tab => {
        const isActive = currentPage === tab.id;
        const labelColor = isActive ? '#D32F2F' : '#666666';
        const emojiColor = tab.isSOS ? '#FFFFFF' : (isActive ? '#D32F2F' : '#666666');

        return (
          <TouchableOpacity
            key={tab.id}
            style={styles.tabItem}
            onPress={() => onSelectPage(tab.id)}
            activeOpacity={0.8}
          >
            {tab.isSOS ? (
              <View style={styles.sosContainer}>
                <View style={styles.sosCircle}>
                  <Text style={styles.sosText}>{tab.label}</Text>
                </View>
                <Text style={[styles.tabLabel, { color: labelColor }]}>
                  {tab.label}
                </Text>
              </View>
            ) : (
              <>
                <Text style={[styles.emoji, { color: emojiColor }]}>
                  {tab.emoji}
                </Text>
                <Text style={[styles.tabLabel, { color: labelColor }]}>
                  {tab.label}
                </Text>
              </>
            )}
          </TouchableOpacity>
        );
      })}
    </View>
  );
};

const styles = StyleSheet.create({
  footer: {
    flexDirection: 'row',
    height: Platform.OS === 'ios' ? 90 : 80,
    backgroundColor: '#ffffff',
    borderTopWidth: 1,
    borderTopColor: '#e0e0e0',
    paddingBottom: Platform.OS === 'ios' ? 20 : 10,
    paddingTop: 20,
  },
  tabItem: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  sosContainer: {
    alignItems: 'center',
  },
  sosCircle: {
    width: 45,
    height: 45,
    borderRadius: 30,
    backgroundColor: '#D32F2F',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 4,
  },
  sosText: {
    color: '#FFFFFF',
    fontSize: 20,
    fontWeight: 'bold',
  },
  emoji: {
    fontSize: 28,
    marginBottom: 4,
  },
  tabLabel: {
    fontSize: 12,
    fontWeight: '500',
  },
});

export default Footer;
