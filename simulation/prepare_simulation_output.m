function out = prepare_simulation_output(out,mustBeNew)
root=fileparts(mfilename('fullpath'));
if nargin<1 || isempty(out)
    out=fullfile(root,'regenerated',['run-' char(datetime('now','Format','yyyyMMdd-HHmmss')) ...
        '-' char(java.util.UUID.randomUUID)]);
end
if nargin<2, mustBeNew=true; end
out=char(java.io.File(char(out)).getCanonicalPath());
reserved={'output','paper_current','data'};
for k=1:numel(reserved)
    protected=char(java.io.File(fullfile(root,reserved{k})).getCanonicalPath());
    assert(~strcmp(out,protected) && ~startsWith(out,[protected filesep]) ...
        && ~startsWith(protected,[out filesep]),'simulation:ArchivedOutput', ...
        'Archived inputs and results cannot be used as a new output directory.');
end
if mustBeNew
    assert(~isfile(out) && ~isfolder(out),'simulation:OutputExists', ...
        'Output already exists. Choose a new result directory.');
end
if ~isfolder(out)
    [ok,message]=mkdir(out);
    assert(ok,'simulation:OutputCreationFailed','Cannot create output directory: %s',message);
end
end
