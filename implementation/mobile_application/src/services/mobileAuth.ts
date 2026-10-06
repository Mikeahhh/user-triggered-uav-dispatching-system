import { DeviceEventEmitter, NativeModules } from 'react-native';

export type CloudIdentity = {
  status: 'SIGNED_OUT' | 'UNBOUND' | 'BOUND';
  uid?: string;
  email?: string;
  displayName?: string;
  phone?: string;
  projectId?: string;
  target?: string;
};

export type BoundCloudIdentity = CloudIdentity & {
  status: 'BOUND'; uid: string; phone: string; projectId: string; target: string;
};

type NativeAuth = {
  state(): Promise<string>;
  signIn(): Promise<string>;
  refreshBinding(): Promise<string>;
  signOut(): Promise<boolean>;
};

const native = (): NativeAuth => {
  if (!NativeModules.MobileAuth) throw new Error('This Android build does not include Google sign-in.');
  return NativeModules.MobileAuth;
};

const decode = (json: string): CloudIdentity => {
  const value = JSON.parse(json) as CloudIdentity;
  if (!['SIGNED_OUT', 'UNBOUND', 'BOUND'].includes(value.status)) {
    throw new Error('Account state is unavailable.');
  }
  if (value.status === 'BOUND' && (!value.uid || !value.projectId || !value.target || !/^\d{3,20}$/.test(value.phone || ''))) {
    throw new Error('The account has no valid administrator binding.');
  }
  return value;
};

export const readCloudIdentity = async (): Promise<CloudIdentity> => decode(await native().state());

export const requireCloudIdentity = async (): Promise<BoundCloudIdentity> => {
  const identity = await readCloudIdentity();
  if (identity.status !== 'BOUND') throw new Error('Sign in and ask the administrator to bind your account in Settings.');
  return identity as BoundCloudIdentity;
};

export const assertCloudIdentity = async (expected: BoundCloudIdentity): Promise<void> => {
  const current = await requireCloudIdentity();
  if (current.uid !== expected.uid || current.projectId !== expected.projectId ||
      current.phone !== expected.phone || current.target !== expected.target) {
    throw new Error('The account changed. Retry from the current account.');
  }
};

export const cloudIdentityKey = (identity: CloudIdentity): string =>
  JSON.stringify([identity.status, identity.projectId || '', identity.uid || '', identity.phone || '']);

export const subscribeCloudIdentity = (listener: (identity: CloudIdentity) => void): (() => void) => {
  const subscription = DeviceEventEmitter.addListener('MobileAuthChanged', (value: string) => {
    try { listener(decode(value)); } catch { listener({ status: 'SIGNED_OUT' }); }
  });
  return () => subscription.remove();
};

const announce = (identity: CloudIdentity): CloudIdentity => {
  DeviceEventEmitter.emit('MobileAuthChanged', JSON.stringify(identity));
  return identity;
};

export const signInToCloud = async (): Promise<CloudIdentity> => announce(decode(await native().signIn()));
export const refreshCloudBinding = async (): Promise<CloudIdentity> => announce(decode(await native().refreshBinding()));
export const signOutOfCloud = async (): Promise<void> => {
  await native().signOut();
  announce({ status: 'SIGNED_OUT' });
};
