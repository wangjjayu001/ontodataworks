export function timeRangeAnswer(start,endExclusive){
 const isDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&!Number.isNaN(Date.parse(value+'T00:00:00Z'))&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
 if(!isDate(start)||!isDate(endExclusive)||start>=endExclusive)return null;
 return {start,end_exclusive:endExclusive};
}

export function clarificationChoice(option){
 if(option===null||typeof option!=='object')return {value:option,label:option,description:null};
 const value=option.value??option.id??option.text??option.label;
 return {value,label:option.label??option.text??value,description:option.description??null};
}
