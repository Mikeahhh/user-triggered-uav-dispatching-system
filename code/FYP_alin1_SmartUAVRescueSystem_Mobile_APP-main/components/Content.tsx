import React from 'react';
import { View, StyleSheet } from 'react-native';
import MapPage from '../pages/MapPage';
import SosPage from '../pages/SosPage';
import SettingPage from '../pages/SettingPage';
import QuickStartPage from '../pages/QuickStartPage';
import EventBookingPage from '../pages/EventBookingPage';
import ProfilePage from '../pages/ProfilePage';
import SystemHealthPage from '../pages/SystemHealthPage';

interface ContentProps {
  currentPage: number;
  onSelectPage: (page: number) => void;
  dbInitialized?: boolean;
}

const Content: React.FC<ContentProps> = ({ currentPage, onSelectPage }) => {
  const renderPage = () => {
    switch (currentPage) {
      case 1: return <MapPage onSelectPage={onSelectPage} />;
      case 2: return <SosPage />;
      case 3: return <SettingPage onSelectPage={onSelectPage} />;
      case 4: return <QuickStartPage />;
      case 5: return <EventBookingPage />;
      case 6: return <ProfilePage />;
      case 7: return <SystemHealthPage onSelectPage={onSelectPage} />;
      default: return <MapPage onSelectPage={onSelectPage} />;
    }
  };

  return <View style={styles.content}>{renderPage()}</View>;
};

const styles = StyleSheet.create({
  content: {
    flex: 0.95,
  },
});

export default Content;
