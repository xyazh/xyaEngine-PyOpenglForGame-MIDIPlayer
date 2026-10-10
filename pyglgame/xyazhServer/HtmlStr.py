import html

class HtmlStr(str):
    def escapeHtml(self):
        return HtmlStr(html.escape(self))
    
    def unescapeHtml(self):
        return HtmlStr(html.unescape(self))
    
    def setColor(self,color:str):
        return HtmlStr("<t style='color: %s;'>%s</t>"%(color,self))
    
if __name__ == "__main__":
    a = HtmlStr("23333").setColor("red")
    print(a)