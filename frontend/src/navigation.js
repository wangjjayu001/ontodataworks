// Keep existing bookmarks while consolidating the workspace entry points.
export function resolveNavigation(value='') {
 const [name='',view=''] = value.replace(/^#/, '').split('/');
 if(name==='publications')return {page:'model',view:'publish'};
 if(name==='governance')return {page:'runs',view:'issues'};
 if(name==='standards')return {page:'semantic',view:''};
 if(name==='lineage')return {page:'assets',view:'lineage'};
 const validView=(name==='model'&&['publish','history'].includes(view))||(name==='runs'&&view==='issues')||(name==='assets'&&view==='lineage');
 return {page:name||'overview',view:validView?view:''};
}
export const navigationHash=({page,view})=>page+(view?'/'+view:'');
