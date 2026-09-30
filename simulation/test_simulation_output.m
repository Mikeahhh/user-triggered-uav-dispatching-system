function test_simulation_output()
root=fileparts(mfilename('fullpath'));
for name={'output','paper_current','data','reference','verification'}
    try
        prepare_simulation_output(fullfile(root,name{1},'must-not-be-created'));
        error('simulation:ExpectedFailure','Archive output was accepted.');
    catch failure
        assert(strcmp(failure.identifier,'simulation:ArchivedOutput'));
    end
end
path=tempname;
out=prepare_simulation_output(path);
cleanup=onCleanup(@()rmdir(out));
assert(isfolder(out));
try
    prepare_simulation_output(path);
    error('simulation:ExpectedFailure','Existing output was accepted.');
catch failure
    assert(strcmp(failure.identifier,'simulation:OutputExists'));
end
fprintf('PASS: archived paths and existing simulation output directories are protected.\n');
end
