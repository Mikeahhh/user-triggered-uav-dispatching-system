function test_flight_clearance()
flat=@(e,n)100+zeros(size(e+n));
accepted=flight_clearance([0;10],[0;0],[102;102],flat,2,5);
assert(accepted.passed && accepted.minimum_clearance_m==2 && accepted.sample_count==3);
rejected=flight_clearance([0;10],[0;0],[101.999;101.999],flat,2,5);
assert(~rejected.passed);
ridge=@(e,n)10*max(0,1-abs(e-5)/5)+zeros(size(n));
rejected=flight_clearance([0;10],[0;0],[10;10],ridge,2,5);
assert(~rejected.passed && rejected.minimum_clearance_m==0);
accepted=flight_clearance([0;10],[0;0],[12;12],ridge,2,5);
assert(accepted.passed && accepted.maximum_sample_spacing_m<=5);
hover=flight_clearance([0;0],[0;0],[102;102],flat,2,5);
assert(hover.passed && hover.maximum_sample_spacing_m==0);
void=flight_clearance([0;10],[0;0],[102;102],@(e,n)NaN(size(e+n)),2,5);
assert(~void.passed);
try
    flight_clearance([0;10],[0;0],[102;NaN],flat,2,5);
    error('simulation:ExpectedFailure','Nonfinite altitude was accepted.');
catch failure
    assert(strcmp(failure.identifier,'simulation:InvalidClearanceInput'));
end
fprintf('PASS: clearance threshold, interior terrain, hover, spacing and invalid samples.\n');
end
