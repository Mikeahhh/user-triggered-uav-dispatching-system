import { NativeModules } from 'react-native';


export const createUavCaptureId = async (): Promise<string> => {
  const create = NativeModules.UavWifi?.createCaptureId || NativeModules.UavCaptureIdentity?.createCaptureId;
  if (typeof create !== 'function') throw new Error('This build cannot create a UAV location transfer');
  const value = await create();
  if (typeof value !== 'string' || value.length !== 32 || !/^[0-9a-f]{32}$/.test(value)) {
    throw new Error('The platform returned an invalid transfer identity');
  }
  return value;
};
