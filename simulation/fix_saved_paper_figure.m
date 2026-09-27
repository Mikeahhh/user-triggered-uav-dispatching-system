function fix_saved_paper_figure(out)

f=openfig(fullfile(out,'terrain_missions.fig'),'invisible');
if isprop(f,'Theme'),f.Theme='light';end
x0=[.055 .360 .665];w=.255;
allAxes=findall(f,'Type','axes');h2=gobjects(1,3);h3=gobjects(1,3);ins=gobjects(1,3);
for j=1:numel(allAxes)
 a=allAxes(j);a.XColor=[.12 .12 .12];a.YColor=[.12 .12 .12];a.ZColor=[.12 .12 .12];
 a.Title.Color=[.05 .05 .05];a.XLabel.Color=[.05 .05 .05];a.YLabel.Color=[.05 .05 .05];a.ZLabel.Color=[.05 .05 .05];
 titleText=string(a.Title.String);
 for k=1:3
  if startsWith(titleText,sprintf('(%c)',char('a'+k-1)))
   a.Position=[x0(k) .615 w .312];h3(k)=a;
  elseif startsWith(titleText,sprintf('(%c)',char('d'+k-1)))
   a.Position=[x0(k) .078 w .476];h2(k)=a;
  end
 end
 if startsWith(titleText,'Target detail')
  [~,k]=min(abs(a.Position(1)-([.047 .359 .671]+.270*.58)));
  a.Position=[x0(k)+w*.58 .328 w*.39 .187];ins(k)=a;
 end
end
map=interp1([0 .15 .35 .6 .8 1],[.79 .86 .65;.64 .76 .48;.57 .66 .41;.63 .58 .40;.70 .64 .51;.83 .80 .71],linspace(0,1,256),'linear');
colormap(h2(3),map);clim(h2(3),[0 700]);
cb=findall(f,'Type','colorbar');cb.Position=[.938 .10 .010 .43];cb.Color=[.12 .12 .12];cb.Label.Color=[.12 .12 .12];cb.Ticks=[0 200 400 600];
lg=findall(f,'Type','legend');lg.TextColor=[.05 .05 .05];lg.Position=[.10 .950 .81 .035];
drawnow;savefig(f,fullfile(out,'terrain_missions.fig'));
exportgraphics(f,fullfile(out,'terrain_missions.png'),'Resolution',450,'BackgroundColor','white');
rec=@(a)struct('xlim',a.XLim,'ylim',a.YLim,'zlim',a.ZLim,'box',a.Box,'data_aspect_ratio',a.DataAspectRatio,'view',a.View,'position',a.Position,'font_size',a.FontSize,'xcolor',a.XColor);
audit=struct('main_3d_axes',arrayfun(rec,h3),'main_top_axes',arrayfun(rec,h2),'target_insets',arrayfun(rec,ins),'colorbar_limits',cb.Limits,'colorbar_uses_terrain_palette',true,'note','Native MATLAB figure, display adjustments only; traces unchanged.');
fid=fopen(fullfile(out,'paper_figure_axes_audit.json'),'w');fwrite(fid,jsonencode(audit,PrettyPrint=true),'char');fclose(fid);
close(f);fprintf('Final paper figure exported.\n');
end
