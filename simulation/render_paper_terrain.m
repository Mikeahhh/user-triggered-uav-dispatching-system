function render_paper_terrain(studyFile,out)

out=prepare_simulation_output(out,false);
load(studyFile,'study');
c=palette();cfg=study.config;
set(groot,'defaultAxesFontName','Helvetica','defaultTextFontName','Helvetica');
set(groot,'defaultAxesXColor',[.12 .12 .12],'defaultAxesYColor',[.12 .12 .12], ...
    'defaultAxesZColor',[.12 .12 .12],'defaultTextColor',[.05 .05 .05]);
fig=figure('Visible','off','Color','w','Position',[40 40 900 510], ...
    'Name','Three complete simulated missions','NumberTitle','off');
if isprop(fig,'Theme'),fig.Theme='light';end
x0=[.055 .360 .665];w=.255;
names={'Mode 1: Event Booking','Mode 2: Quick Start','Mode 3: SOS'};
h3=gobjects(1,3);h2=gobjects(1,3);ins=gobjects(1,3);
for k=1:3
    m=study.missions(k);
    ax=axes(fig,'Position',[x0(k) .615 w .312]);h3(k)=ax;
    drawTerrain3(ax,study,1);drawMission(ax,m,study,true,c);configure3(ax,study);
    title(ax,sprintf('(%c) %s',char('a'+k-1),names{k}),'FontSize',10,'FontWeight','bold');
    xlabel(ax,'East (km)','FontSize',9);ylabel(ax,'North (km)','FontSize',9);
    zlabel(ax,'Elevation (m)','FontSize',9);
    xticks(ax,[-1 1 3 5]);yticks(ax,[-1 1 3 5]);
    if k==1
        l1=plot3(ax,NaN,NaN,NaN,'-','Color',c.outbound,'LineWidth',1.7);
        l2=plot3(ax,NaN,NaN,NaN,'-','Color',c.search,'LineWidth',1.7);
        l3=plot3(ax,NaN,NaN,NaN,'--','Color',c.return,'LineWidth',1.7);
        l4=plot3(ax,NaN,NaN,NaN,'s','Color',c.launch,'MarkerFaceColor',c.launch,'MarkerSize',4);
        l5=plot3(ax,NaN,NaN,NaN,'p','Color',c.target,'MarkerFaceColor',c.target,'MarkerSize',6);
        lg=legend(ax,[l1 l2 l3 l4 l5],{'Outbound / route','SOS search','Return / landing','Launch / landing','Target'}, ...
            'Orientation','horizontal','Box','off','FontSize',9);
        lg.TextColor=[.05 .05 .05];lg.Units='normalized';lg.Position=[.10 .950 .81 .035];
    end
    ax=axes(fig,'Position',[x0(k) .078 w .476]);h2(k)=ax;
    drawTerrain2(ax,study);drawMission(ax,m,study,false,c);
    configure2(ax,cfg.scene_east_limits_m/1000,cfg.scene_north_limits_m/1000);
    title(ax,sprintf('(%c) Complete mission: plan view',char('d'+k-1)), ...
        'FontSize',10,'FontWeight','bold');
    xlabel(ax,'East (km)','FontSize',9);ylabel(ax,'North (km)','FontSize',9);
    xticks(ax,[-1 1 3 5]);yticks(ax,[-1 1 3 5]);
    p=study.scenario.target_en_m/1000;r=cfg.target_zoom_half_width_m/1000;
    rectangle(ax,'Position',[p(1)-r p(2)-r 2*r 2*r], ...
        'EdgeColor',[.65 .22 .2],'LineStyle',':','LineWidth',1);
    iax=axes(fig,'Position',[x0(k)+w*.58 .328 w*.39 .187]);ins(k)=iax;
    drawTerrain2(iax,study);drawMission(iax,m,study,false,c);
    configure2(iax,p(1)+[-r r],p(2)+[-r r]);
    set(iax,'FontSize',7.5,'LineWidth',.8,'XTick',p(1)+[-.2 .2], ...
        'YTick',p(2)+[-.2 .2]);
    xticklabels(iax,{'-200','200'});yticklabels(iax,{'-200','200'});
    title(iax,'Target detail (m)','FontSize',7.5,'FontWeight','bold');
end
colormap(h2(3),terrainColors(256));clim(h2(3),[0 700]);
cb=colorbar(h2(3),'Position',[.938 .10 .010 .43]);
cb.Label.String='Ground elevation (m)';cb.FontSize=8;cb.Ticks=[0 200 400 600];cb.Color=[.12 .12 .12];

set(h2(3),'Position',[x0(3) .078 w .476]);
drawnow;
exportFigure(fig,fullfile(out,'terrain_missions'));
writeJson(fullfile(out,'paper_figure_axes_audit.json'),struct( ...
    'main_3d_axes',arrayfun(@axisRecord,h3), ...
    'main_top_axes',arrayfun(@axisRecord,h2),'target_insets',arrayfun(@axisRecord,ins), ...
    'source_study',studyFile,'note','Presentation-only rendering of the saved study.'));
close(fig);
end

function drawTerrain3(ax,s,skip)
t=s.terrain;ind=1:skip:size(t.height_msl_m,1);
e=t.east_grid_m(ind,ind)/1000;n=t.north_grid_m(ind,ind)/1000;z=t.height_msl_m(ind,ind);
land=z;land(z<=.5)=NaN;
water=zeros(size(z));water(z>.5)=NaN;
hold(ax,'on');
surf(ax,e,n,water,'FaceColor',[.64 .82 .87],'EdgeColor','none','FaceLighting','none');
surf(ax,e,n,land/1000,land,'FaceColor','interp','EdgeColor','none', ...
    'AmbientStrength',.7,'DiffuseStrength',.45,'SpecularStrength',.02);
colormap(ax,terrainColors(256));clim(ax,[0 700]);
contour3(ax,e,n,land/1000,0:.1:.7,'LineColor',[.35 .4 .31],'LineWidth',.35);
lighting(ax,'gouraud');camlight(ax,35,65);
set(findobj(ax,'Type','surface'),'AmbientStrength',.9,'DiffuseStrength',.25,'SpecularStrength',.02);
end

function drawTerrain2(ax,s)
t=s.terrain;z=t.height_msl_m;
map=terrainColors(256);
ix=1+round(min(1,max(0,z/700))*255);
rgb=reshape(map(ix(:),:),[size(z) 3]);
[gx,gy]=gradient(z,s.config.terrain_plot_step_m);
shade=.86+.14*(-.7*gx+.7*gy+1)./sqrt(gx.^2+gy.^2+1);
rgb=min(1,max(0,rgb.*shade));
for channel=1:3
    layer=rgb(:,:,channel);water=[.71 .86 .9];layer(z<=.5)=water(channel);rgb(:,:,channel)=layer;
end
hold(ax,'on');
image(ax,'XData',s.config.scene_east_limits_m/1000, ...
    'YData',s.config.scene_north_limits_m/1000,'CData',rgb);
land=z;land(z<=.5)=NaN;
contour(ax,t.east_grid_m/1000,t.north_grid_m/1000,land,50:100:650, ...
    'LineColor',[.43 .46 .34],'LineWidth',.35);
contour(ax,t.east_grid_m/1000,t.north_grid_m/1000,z,[.5 .5], ...
    'LineColor',[.18 .42 .49],'LineWidth',.85);
end

function drawMission(ax,m,s,is3,c)
r=m.trace;
cut=m.return_start_s;
outbound=r.time_s<=cut;
returning=r.time_s>=cut;
if m.mode==3
    transit=outbound & r.time_s<=m.target_arrival_s;
    search=outbound & r.time_s>=m.target_arrival_s;
else
    transit=outbound;search=false(height(r),1);
end
if is3
    plot3(ax,r.east_m(transit)/1000,r.north_m(transit)/1000,r.uav_msl_m(transit)/1000, ...
        '-','Color',c.outbound,'LineWidth',1.7);
    plot3(ax,r.east_m(search)/1000,r.north_m(search)/1000,r.uav_msl_m(search)/1000, ...
        '-','Color',c.search,'LineWidth',1.7);
    plot3(ax,r.east_m(returning)/1000,r.north_m(returning)/1000,r.uav_msl_m(returning)/1000, ...
        '--','Color',c.return,'LineWidth',1.5);
    launchZ=s.terrain.launch_ground_msl_m/1000;
    plot3(ax,0,0,launchZ,'s','MarkerFaceColor',c.launch,'MarkerEdgeColor','w','MarkerSize',5,'LineWidth',1);
    p=s.scenario.target_en_m/1000;z=s.scenario.target_ground_msl_m/1000;
    plot3(ax,p(1),p(2),z,'p','MarkerFaceColor',c.target,'MarkerEdgeColor','w','MarkerSize',7,'LineWidth',.7);
    plot3(ax,[p(1) p(1)],[p(2) p(2)],[z z+s.config.altitude_agl_m/1000],':', ...
        'Color',c.target,'LineWidth',1.1);
else
    plot(ax,r.east_m(transit)/1000,r.north_m(transit)/1000,'-','Color',c.outbound,'LineWidth',1.5);
    plot(ax,r.east_m(search)/1000,r.north_m(search)/1000,'-','Color',c.search,'LineWidth',1.4);
    plot(ax,r.east_m(returning)/1000,r.north_m(returning)/1000,'--','Color',c.return,'LineWidth',1.4);
    plot(ax,m.waypoints(:,1)/1000,m.waypoints(:,2)/1000,'o','MarkerSize',2.5, ...
        'MarkerFaceColor','w','MarkerEdgeColor',c.outbound,'LineWidth',.7);
    plot(ax,0,0,'s','MarkerFaceColor',c.launch,'MarkerEdgeColor','w','MarkerSize',5,'LineWidth',1);
    p=s.scenario.target_en_m/1000;
    plot(ax,p(1),p(2),'p','MarkerFaceColor',c.target,'MarkerEdgeColor','w','MarkerSize',7,'LineWidth',.7);
end
end

function configure3(ax,s)
xlim(ax,s.config.scene_east_limits_m/1000);ylim(ax,s.config.scene_north_limits_m/1000);
zlim(ax,[-.01 .78]);zticks(ax,[0 .6]);zticklabels(ax,{'0','600'});
set(ax,'Box','on','BoxStyle','full','LineWidth',.9,'FontSize',9, ...
    'GridAlpha',.16,'Projection','orthographic','Color','w');
grid(ax,'on');daspect(ax,[1 1 1]);view(ax,36,43);
end

function configure2(ax,xl,yl)
set(ax,'YDir','normal','Box','on','LineWidth',.95,'Layer','top', ...
    'FontSize',9,'GridAlpha',.12,'Color','w');
xlim(ax,xl);ylim(ax,yl);daspect(ax,[1 1 1]);grid(ax,'on');
end

function rec=axisRecord(ax)
rec=struct('xlim',ax.XLim,'ylim',ax.YLim,'zlim',ax.ZLim,'box',ax.Box, ...
    'data_aspect_ratio',ax.DataAspectRatio,'view',ax.View,'position',ax.Position);
end

function exportFigure(fig,stem)
savefig(fig,[stem '.fig']);
exportgraphics(fig,[stem '.png'],'Resolution',450,'BackgroundColor','white');

fprintf('Exported %s\n',stem);
end

function c=palette()
c.outbound=[.04 .33 .64];c.search=[.91 .49 .04];c.return=[.69 .13 .48];
c.launch=[.08 .12 .15];c.target=[.8 .13 .12];
end

function map=terrainColors(n)
stops=[0 .15 .35 .6 .8 1];
rgb=[.79 .86 .65;.64 .76 .48;.57 .66 .41;.63 .58 .40;.70 .64 .51;.83 .80 .71];
map=interp1(stops,rgb,linspace(0,1,n),'linear');
end

function writeJson(path,value)
fid=fopen(path,'w');cleanup=onCleanup(@()fclose(fid));
fwrite(fid,jsonencode(value,PrettyPrint=true),'char');
end
