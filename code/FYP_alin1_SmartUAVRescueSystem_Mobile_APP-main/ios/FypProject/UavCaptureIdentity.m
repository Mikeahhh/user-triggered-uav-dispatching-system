#import <Foundation/Foundation.h>
#import <React/RCTBridgeModule.h>

// A platform-generated UUID identifies the saved transfer; it grants no access.
@interface UavCaptureIdentity : NSObject <RCTBridgeModule>
@end

@implementation UavCaptureIdentity
RCT_EXPORT_MODULE(UavCaptureIdentity)

+ (BOOL)requiresMainQueueSetup { return NO; }

RCT_REMAP_METHOD(createCaptureId,
                 createCaptureIdWithResolver:(RCTPromiseResolveBlock)resolve
                 rejecter:(RCTPromiseRejectBlock)reject)
{
  NSString *identifier = [[[NSUUID UUID] UUIDString] lowercaseString];
  resolve([identifier stringByReplacingOccurrencesOfString:@"-" withString:@""]);
}
@end
