import { createContext } from 'react';
import { assertCloudIdentity, BoundCloudIdentity, CloudIdentity } from './mobileAuth';

export const CloudAccountContext = createContext<CloudIdentity>({ status: 'SIGNED_OUT' });

// Keep form ownership tied to the account that rendered it, including delayed callbacks.
export const requireRenderedAccount = async (identity: CloudIdentity): Promise<BoundCloudIdentity> => {
  if (identity.status !== 'BOUND') throw new Error('Sign in and refresh your administrator binding in Settings.');
  const expected = identity as BoundCloudIdentity;
  await assertCloudIdentity(expected);
  return expected;
};
